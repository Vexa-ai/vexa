/**
 * Google Meet speaker attribution — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright imports). Consumed by BOTH:
 *  - the bot: bundled into browser-utils.global.js, instantiated in-page by
 *    googlemeet/recording.ts; Node's speaker-identity.ts delegates resolution
 *    here via page.evaluate.
 *  - the extension: imported directly by vexa-extension/src/inpage.ts.
 *
 * Algorithm (vote/lock, inherited from the bot's speaker-identity.ts):
 *  - Audio chunks arrive per track (per participant media element). The host
 *    calls reportTrackAudio(trackIndex) on every audible chunk.
 *  - Every poll tick: read who is visibly speaking from participant tiles.
 *    Exactly 1 speaker lit → tracks with current audio vote 1.0 for that name;
 *    2 lit → 0.5 each; 0 or 3+ → no vote.
 *  - Lock at >=2 votes with >=70% share. One-name-per-track, one-track-per-name.
 *  - NO participant-order fallback: an unmapped track stays unmapped ("") —
 *    wrong names are worse than missing names.
 *
 * Self-healing speaking detection:
 *  Meet's speaking-indicator CSS classes are obfuscated and rot with UI pushes
 *  (the historic failure mode: zero votes forever, then bad fallbacks). This
 *  module watches class mutations across tiles and correlates them with real
 *  audio arrivals; if the known classes go silent while audio flows, the most
 *  audio-correlated mutating class is adopted as a speaking indicator (logged
 *  loudly, capped). getState() exposes the full forensic dump.
 */

export interface GmeetSpeakersOptions {
  /** Local participant's display name (bot name / data-self-name). Excluded from candidates. */
  selfName?: string;
  /** Fired when a track locks to a name (and if a lock ever changes). */
  onName?: (trackIndex: number, name: string) => void;
  /** Log sink (defaults to console.log). */
  log?: (msg: string) => void;
  lockThreshold?: number;   // default 2
  lockRatio?: number;       // default 0.7
  pollMs?: number;          // default 500
  /** How recent (ms) a track's audio must be to vote. Default 700. */
  audioWindowMs?: number;
  /** Adopt a learned indicator class only after known classes have been silent this long. Default 10s. */
  learnAfterSilentMs?: number;
  /** Audio-correlated mutation count required to adopt a class. Default 3. */
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
  votes: Record<number, Record<string, number>>;
  locks: Record<number, string>;
  participantCount: number;
  selectorStats: {
    knownClassHits: Record<string, number>;
    learnedClasses: string[];
    /** class → audio-correlated mutation count (learning evidence) */
    candidateScores: Record<string, number>;
    lastKnownHitMs: number;
  };
}

export interface GmeetSpeakers {
  reportTrackAudio(trackIndex: number): void;
  /** Locked name, else top-voted untaken name, else null. */
  resolve(trackIndex: number): { name: string | null; locked: boolean };
  isLocked(trackIndex: number): boolean;
  getState(): GmeetSpeakersState;
  invalidate(trackIndex?: number): void;
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
  const lockThreshold = opts.lockThreshold ?? 2;
  const lockRatio = opts.lockRatio ?? 0.7;
  const pollMs = opts.pollMs ?? 500;
  const audioWindowMs = opts.audioWindowMs ?? 700;
  const learnAfterSilentMs = opts.learnAfterSilentMs ?? 10_000;
  const learnMinScore = opts.learnMinScore ?? 3;

  const trackVotes = new Map<number, Map<string, number>>();
  const locks = new Map<number, string>();
  const announced = new Map<number, string>();
  const trackLastAudio = new Map<number, number>();
  let lastParticipantCount = 0;

  // Self-healing state
  const knownClassHits: Record<string, number> = {};
  const learnedClasses: string[] = [];
  const candidateScores = new Map<string, number>();
  let lastKnownHitMs = Date.now();
  /** Recent class additions: class → last-added timestamp (rolling). */
  const recentClassAdds = new Map<string, number>();

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

  // ── Vote / lock ─────────────────────────────────────────────────

  function nameTaken(name: string, except: number): boolean {
    for (const [i, n] of locks) if (i !== except && n === name) return true;
    return false;
  }

  function vote(index: number, name: string, weight: number): void {
    if (locks.has(index) || nameTaken(name, index)) return;
    let v = trackVotes.get(index);
    if (!v) { v = new Map(); trackVotes.set(index, v); }
    v.set(name, (v.get(name) || 0) + weight);
    const total = [...v.values()].reduce((a, b) => a + b, 0);
    const top = [...v.entries()].sort((a, b) => b[1] - a[1])[0];
    if (top[1] >= lockThreshold && top[1] / total >= lockRatio && !nameTaken(top[0], index)) {
      locks.set(index, top[0]);
      log(`[GmeetSpeakers] LOCKED track ${index} = "${top[0]}" (${top[1].toFixed(1)}/${total.toFixed(1)} votes)`);
    }
  }

  // ── Self-healing: learn speaking classes from audio correlation ──

  const observer = new MutationObserver(muts => {
    const now = Date.now();
    for (const m of muts) {
      if (m.type !== 'attributes' || m.attributeName !== 'class') continue;
      const el = m.target as HTMLElement;
      const old = new Set(String(m.oldValue || '').split(/\s+/).filter(Boolean));
      el.classList.forEach(c => {
        if (!old.has(c) && !LEARN_BLOCKLIST.test(c) && c.length <= 24) recentClassAdds.set(c, now);
      });
    }
    if (recentClassAdds.size > 200) {
      for (const [c, t] of recentClassAdds) if (now - t > 5000) recentClassAdds.delete(c);
    }
  });
  try {
    observer.observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true, subtree: true });
  } catch { /* body not ready; poll loop still works with known classes */ }

  function creditAudioCorrelation(now: number): void {
    // An audible chunk just arrived: classes added in the last 400ms are
    // candidate speaking indicators.
    for (const [cls, t] of recentClassAdds) {
      if (now - t <= 400 && !KNOWN_SPEAKING_CLASSES.includes(cls) && !learnedClasses.includes(cls)) {
        candidateScores.set(cls, (candidateScores.get(cls) || 0) + 1);
      }
    }
  }

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
      log(`[GmeetSpeakers] ⚠ known speaking classes silent ${((now - lastKnownHitMs) / 1000).toFixed(0)}s — LEARNED indicator class "${best[0]}" (audio-correlated ×${best[1]})`);
    }
  }

  // ── Main loop ───────────────────────────────────────────────────

  const timer = setInterval(() => {
    const now = Date.now();
    const tiles = scanTiles();

    // Participant-count change: clear UNLOCKED votes only. (The legacy bot
    // cleared locks too, discarding correct mappings on every join/leave.)
    const count = tiles.length;
    if (lastParticipantCount > 0 && count !== lastParticipantCount) {
      trackVotes.clear();
      log(`[GmeetSpeakers] participant count ${lastParticipantCount} → ${count}; cleared unlocked votes (locks kept)`);
    }
    lastParticipantCount = count;

    const speaking = [...new Set(tiles.filter(t => !t.self && t.speaking && t.name).map(t => t.name as string))];
    if (speaking.length >= 1 && speaking.length <= 2) {
      for (const [index, last] of trackLastAudio) {
        if (now - last > audioWindowMs) continue;
        if (locks.has(index)) continue;
        if (speaking.length === 1) vote(index, speaking[0], 1.0);
        else for (const n of speaking) vote(index, n, 0.5);
      }
    }

    maybeLearn(now);

    for (const [index, name] of locks) {
      if (announced.get(index) !== name) {
        announced.set(index, name);
        try { opts.onName?.(index, name); } catch { /* consumer error */ }
      }
    }
  }, pollMs);

  return {
    reportTrackAudio(trackIndex: number): void {
      const now = Date.now();
      trackLastAudio.set(trackIndex, now);
      creditAudioCorrelation(now);
    },
    resolve(trackIndex: number): { name: string | null; locked: boolean } {
      const locked = locks.get(trackIndex);
      if (locked) return { name: locked, locked: true };
      const v = trackVotes.get(trackIndex);
      if (v && v.size > 0) {
        const sorted = [...v.entries()].sort((a, b) => b[1] - a[1]);
        for (const [name] of sorted) if (!nameTaken(name, trackIndex)) return { name, locked: false };
      }
      return { name: null, locked: false };
    },
    isLocked(trackIndex: number): boolean {
      return locks.has(trackIndex);
    },
    getState(): GmeetSpeakersState {
      const tiles = scanTiles();
      return {
        tiles,
        speakingNow: tiles.filter(t => !t.self && t.speaking && t.name).map(t => t.name as string),
        votes: Object.fromEntries([...trackVotes.entries()].map(([i, v]) => [i, Object.fromEntries(v)])),
        locks: Object.fromEntries(locks),
        participantCount: tiles.length,
        selectorStats: {
          knownClassHits: { ...knownClassHits },
          learnedClasses: [...learnedClasses],
          candidateScores: Object.fromEntries(candidateScores),
          lastKnownHitMs,
        },
      };
    },
    invalidate(trackIndex?: number): void {
      if (trackIndex === undefined) {
        trackVotes.clear(); locks.clear(); announced.clear();
        log('[GmeetSpeakers] all mappings invalidated');
      } else {
        trackVotes.delete(trackIndex); locks.delete(trackIndex); announced.delete(trackIndex);
        log(`[GmeetSpeakers] track ${trackIndex} invalidated`);
      }
    },
    destroy(): void {
      clearInterval(timer);
      try { observer.disconnect(); } catch { /* already gone */ }
    },
  };
}
