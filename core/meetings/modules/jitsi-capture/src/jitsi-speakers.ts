/**
 * Jitsi Meet dominant-speaker attribution — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright, no cross-file imports — the bot
 * bundles this file standalone). Consumed by the bot (bundled into
 * browser-utils.global.js; the capture bridge instantiates it post-admission)
 * and importable by any other embedder of a Jitsi page.
 *
 * Signal, layered newest-truth-first:
 *  1. The app's own redux state — `APP.store.getState()['features/base/participants']`
 *     carries `dominantSpeaker` (participant id) and the participant name map. This
 *     is the SAME state jitsi's UI renders from, needs no filmstrip in the DOM, and
 *     survives reskins. Polled (the store shape is stable across versions; the
 *     subscribe API is not worth coupling to).
 *  2. DOM fallback for builds that strip the APP global: the active tile carries a
 *     `dominant-speaker` class; its display-name node carries the name.
 *
 * Debounced speaking start/stop events per participant feed the
 * ChunkedTranscriber's name binder as 'dom-active' hints (same protocol as the
 * Teams/Zoom watchers).
 */

export interface JitsiSpeakersOptions {
  /** Local participant / bot display name — the bot's own tile is never reported. */
  selfName?: string;
  /** Speaking state change: isEnd=false → started speaking, isEnd=true → stopped.
   *  tMs = wall-clock at emit. */
  onSpeaking: (name: string, id: string, isEnd: boolean, tMs: number) => void;
  log?: (msg: string) => void;
  /** Poll interval (ms). Default 400 — dominant-speaker changes are second-scale. */
  pollMs?: number;
}

export interface JitsiSpeakers {
  destroy(): void;
  getState(): { mode: "redux" | "dom" | null; current: string | null; changes: number };
}

// The dominant tile's marker class (stock jitsi filmstrip) + display-name nodes.
export const jitsiDominantTileSelectors: string[] = [
  ".dominant-speaker",
  '[class*="dominant-speaker"]',
];
export const jitsiTileNameSelectors: string[] = [
  ".displayname",
  '[class*="displayname" i]',
  '[data-testid="videoContainerName"]',
  '[class*="display-name"]',
];

/** Defensive read of the app's participants state → the dominant speaker's name+id. */
function dominantFromRedux(): { id: string; name: string } | null {
  try {
    const app = (globalThis as any).APP;
    const state = app?.store?.getState?.();
    const p = state?.["features/base/participants"];
    const id: string | undefined = p?.dominantSpeaker;
    if (!id) return null;
    // `remote` is a Map<id, participant>; `local` a plain participant object.
    let participant: any = null;
    if (p.local?.id === id) participant = p.local;
    else if (typeof p.remote?.get === "function") participant = p.remote.get(id);
    const name = (participant?.name || "").trim();
    return name ? { id: String(id), name } : null;
  } catch {
    return null;
  }
}

/** DOM fallback: the tile marked dominant-speaker → its display-name text. */
function dominantFromDom(): { id: string; name: string } | null {
  try {
    for (const tileSel of jitsiDominantTileSelectors) {
      const tile = document.querySelector(tileSel);
      if (!tile) continue;
      for (const nameSel of jitsiTileNameSelectors) {
        const name = tile.querySelector(nameSel)?.textContent?.trim();
        if (name) return { id: `dom:${name}`, name };
      }
    }
    return null;
  } catch {
    return null;
  }
}

export function createJitsiSpeakers(opts: JitsiSpeakersOptions): JitsiSpeakers {
  const log = opts.log || (() => {});
  const self = (opts.selfName || "").trim().toLowerCase();
  let current: { id: string; name: string } | null = null;
  let mode: "redux" | "dom" | null = null;
  let changes = 0;

  const emit = (name: string, id: string, isEnd: boolean) => {
    try { opts.onSpeaking(name, id, isEnd, Date.now()); } catch { /* never break capture */ }
  };

  const tick = () => {
    let d = dominantFromRedux();
    if (d) mode = "redux";
    else { d = dominantFromDom(); if (d) mode = mode ?? "dom"; }

    // The bot's own speech (TTS) must not name segments after the bot.
    if (d && self && d.name.trim().toLowerCase() === self) d = null;

    const changed = (d?.id ?? null) !== (current?.id ?? null);
    if (!changed) return;
    if (current) emit(current.name, current.id, true);
    if (d) {
      emit(d.name, d.id, false);
      log(`dominant speaker → ${d.name}`);
    }
    current = d;
    changes++;
  };

  const poll = setInterval(tick, opts.pollMs ?? 400);
  tick();

  return {
    destroy() {
      clearInterval(poll);
      if (current) emit(current.name, current.id, true);
      current = null;
    },
    getState() {
      return { mode, current: current?.name ?? null, changes };
    },
  };
}
