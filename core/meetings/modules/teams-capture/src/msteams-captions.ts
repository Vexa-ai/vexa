/**
 * MS Teams live closed-captions (CC) reader — an ALTERNATIVE speaker-attribution
 * SOURCE, reopened from the 0.10 era. Pure browser code (no Node, no Playwright,
 * no cross-package imports — the bot bundles this file standalone), same shape as
 * `msteams-speakers.ts` and `teams-chat.ts`.
 *
 * WHAT IT IS. When a Teams meeting has live captions ON, Teams renders each
 * utterance as an `author` + `closed-caption-text` pair inside a caption
 * renderer. Teams' own ASR attributes those to a named participant — which is a
 * WHO signal our mixed lane cannot otherwise get (one server-side audio mix, no
 * per-participant tracks). This module reads that stream and emits
 * `{ speaker, text, tMs, stable }`.
 *
 * WHAT IT IS NOT (this iteration). The events are DIAGNOSTIC observation data:
 * they do NOT feed the transcript, and they do NOT feed the mixed-pipeline name
 * binder. Nothing about product behaviour changes by adding this module. The
 * first live meeting exists to answer ONE question — do the 0.10 selectors still
 * match the current Teams client? — which is why every availability transition is
 * emitted as a typed observation rather than the module failing silent. A watcher that finds nothing and says nothing is the failure
 * mode this whole brick was rewritten to stop (see msteams-speakers.ts: a
 * hardcoded indicator produced zero transitions across a live 13-minute meeting
 * and nothing in the logs said so).
 *
 * CC IS FLAKE-CLASS BY DESIGN — that is the founder's ruling, and it is the
 * module's central assumption rather than an edge case. Captions can fail to
 * enable, vanish mid-meeting, or be blocked by tenant policy at any moment. So:
 *   • the voice-level-outline watcher (`createTeamsSpeakers`) runs in PARALLEL at
 *     all times and is never replaced by this one — a CC outage must be entirely
 *     invisible to the DOM path;
 *   • every availability transition is typed and emitted — `captions-active`,
 *     `captions-lost`, `captions-recovered`, `captions-absent` — so a downstream
 *     consumer can weigh the two sources per moment instead of assuming either;
 *   • recovery is automatic: the poll never stops, so a wrapper that reappears
 *     re-arms the reader and says `captions-recovered` (the rescan discipline the
 *     speakers module already uses for re-rendered tiles).
 * Absent captions, an unknown DOM shape, a throwing consumer — every one of them
 * degrades to "no caption events" and NEVER to a failed join or a broken capture
 * path. (Switching captions ON at join is the BOT's job, not this module's: it is
 * a Playwright-side menu interaction and lives in the capture bridge.)
 *
 * SELECTORS ARE HISTORY, NOT GUESSES. `[data-tid="closed-caption-renderer-wrapper"]`,
 * `[data-tid="author"]` and `[data-tid="closed-caption-text"]` are the atoms the
 * 0.10 bot ran on, verified 2026-03-19 against BOTH host and guest views:
 *   HOST:  wrapper > window-wrapper > virtual-list-content > items-renderer > ChatMessageCompact > author + text
 *   GUEST: wrapper > window-wrapper > virtual-list-content > (div) > author + text   (NO items-renderer)
 * Only the wrapper and the two leaf atoms were stable across both, so authors and
 * texts are queried directly under the wrapper and PAIRED BY DOCUMENT ORDER —
 * never through the host-only `closed-captions-v2-items-renderer`.
 * (Origin: v0.10.7 services/vexa-bot/core/src/platforms/msteams/{selectors,recording}.ts.)
 *
 * DEDUP / STABILIZATION. Teams mutates a caption entry IN PLACE as its ASR
 * refines it — the same utterance fires dozens of mutations, growing word by word
 * and re-punctuating. Emitting per mutation would produce keystroke-level noise,
 * so an entry is emitted when it has STOPPED changing for `stabilizeMs`, or when
 * a new entry supersedes it (it can never change again). `stable:false` marks the
 * single best-effort flush at `destroy()` of an entry still inside its window —
 * the tail of a meeting, where waiting for stabilization would just drop it.
 *
 * NEVER FABRICATE. An entry whose author node is empty, or whose author text is
 * not name-shaped (the SAME `isTeamsDisplayNameCandidate` guard every other name
 * path in this module uses), yields a typed `caption-speaker-unresolved`
 * observation and NO caption event. Unknown stays unknown — a diagnostic that
 * becomes a speaker name is a fabricated speaker name.
 */
import { isSelfDisplayName, isTeamsDisplayNameCandidate } from './msteams-speakers.js';

/** Teams live-caption selectors. The wrapper + the two leaf atoms are the 0.10
 * pair, verified live on host AND guest views; the rest are candidates carried so
 * a client that renamed one still matches and SAYS WHICH — the activation
 * observation reports the selectors that actually matched. */
export const teamsCaptionSelectors = {
  /** Top-level caption containers, most-trusted first. Present only when captions
   *  are enabled AND someone has spoken. */
  wrappers: [
    '[data-tid="closed-caption-renderer-wrapper"]',
    '[data-tid="closed-caption-v2-virtual-list-content"]',
    '[data-tid="closed-captions-renderer"]',
    '[data-tid*="closed-caption"]',
  ] as string[],
  /** Speaker-name atoms, most-trusted first. Paired by document order with `texts`. */
  authors: [
    '[data-tid="author"]',
    '[data-tid="caption-author"]',
    '[data-tid*="author"]',
  ] as string[],
  /** Caption-text atoms, most-trusted first. Paired by document order with `authors`. */
  texts: [
    '[data-tid="closed-caption-text"]',
    '[data-tid*="caption-text"]',
  ] as string[],
};

/** One Teams caption entry. `stable:true` — the entry stopped changing (either it
 * sat unchanged for `stabilizeMs`, or a newer entry superseded it, so it can never
 * change again). `stable:false` — a best-effort flush at `destroy()` of an entry
 * still mid-refinement. DIAGNOSTIC in this iteration: never a transcript segment,
 * never a name-binder hint. */
export interface TeamsCaptionEvent {
  speaker: string;
  text: string;
  /** Epoch ms at emission (the same clock domain the bridge stamps audio with). */
  tMs: number;
  stable: boolean;
}

/** The caption renderer is present and this source is LIVE — `captions-active` on
 * first activation, `captions-recovered` when it comes back after a loss (the two
 * carry the same payload; the type is the transition). Carries the selectors that
 * matched, so a Teams rename is visible from one line of one live run rather than
 * inferred from silence. */
export interface TeamsCaptionsActiveObservation {
  type: 'captions-active' | 'captions-recovered';
  platform: 'teams';
  signal: 'closed-caption';
  wrapperSelector: string;
  authorSelector: string | null;
  textSelector: string | null;
  /** How many times this source has dropped out so far (0 on first activation). */
  losses: number;
  tMs: number;
}

/** The caption renderer WAS live and is now gone — captions switched off
 * mid-meeting, the panel re-mounted, or the client changed shape. The DOM
 * outline path is unaffected and keeps producing hints; this says the second
 * source went quiet, so nobody downstream reads its silence as consensus. */
export interface TeamsCaptionsLostObservation {
  type: 'captions-lost';
  platform: 'teams';
  signal: 'closed-caption';
  reason: 'renderer-lost';
  /** Per-candidate wrapper match counts — a wrapper rename shows up here as all-zero. */
  candidates: Array<{ sel: string; count: number }>;
  /** Captions emitted before the drop-out — separates a source that WAS working from one that
   *  never did. */
  emitted: number;
  tMs: number;
}

/** No caption renderer ever appeared within the detection window: captions are
 * off (the enable step failed or tenant policy blocks them), or every candidate
 * selector is stale. `candidates` tells those two apart on the first live run. */
export interface TeamsCaptionsAbsentObservation {
  type: 'captions-absent';
  platform: 'teams';
  signal: 'closed-caption';
  reason: 'renderer-missing';
  candidates: Array<{ sel: string; count: number }>;
  tMs: number;
}

/** A caption entry that carried text but no usable speaker. The event is WITHHELD;
 * this says so. Carries no caption text and no DOM text — a diagnostic must never
 * become a channel for content OR for a guessed name. */
export interface TeamsCaptionSpeakerUnresolvedObservation {
  type: 'caption-speaker-unresolved';
  platform: 'teams';
  signal: 'closed-caption';
  reason: 'author-empty' | 'author-not-name-shaped';
  tMs: number;
}

export type TeamsCaptionObservation =
  | TeamsCaptionsActiveObservation
  | TeamsCaptionsLostObservation
  | TeamsCaptionsAbsentObservation
  | TeamsCaptionSpeakerUnresolvedObservation;

/** Coverage + liveness of the caption signal, for a caller that wants to log it.
 * `present` + `losses` + `recoveries` are what make a flake-class source legible:
 * "captions were live for 4 minutes, dropped twice" is a fact a consumer can
 * weigh; "no captions arrived" is not. */
export interface TeamsCaptionsHealth {
  /** Is a caption renderer matched right now. */
  present: boolean;
  /** Times the renderer went away after being live. */
  losses: number;
  /** Times it came back. */
  recoveries: number;
  /** Which wrapper selector matched (null when none has ever matched). */
  wrapperSelector: string | null;
  /** Author/text atoms under the wrapper on the last scan (skew ⇒ pairing risk). */
  authors: number;
  texts: number;
  /** Caption events emitted. */
  emitted: number;
  /** Entries withheld because the speaker could not be resolved. */
  unresolved: number;
  /** Entries dropped because Teams attributed them to us (the bot). */
  self: number;
  /** Epoch ms of the last emitted caption, or null. */
  lastCaptionAtMs: number | null;
}

export interface TeamsCaptionsOptions {
  /** Every stabilized caption entry. Consumer throws are swallowed. */
  onCaption: (event: TeamsCaptionEvent) => void;
  /** Typed producer observations. DIAGNOSTICS — never turn one into a name. */
  onObservation?: (observation: TeamsCaptionObservation) => void;
  log?: (msg: string) => void;
  /** Local participant / bot display name — captions Teams attributes to it are dropped. */
  selfName?: string;
  /** How long an entry must stop changing before it is emitted (ms). Default 900 —
   *  longer than Teams' ASR refinement cadence, short enough to stay near-live. */
  stabilizeMs?: number;
  /** Scan cadence (ms). Default 250 (0.10 ran a 200 ms backup poll behind the
   *  MutationObserver because the virtualized list drops mutations). */
  pollMs?: number;
  /** How long to wait before concluding no renderer exists (ms). Default 30000 —
   *  captions can be switched on mid-meeting, and the wrapper only mounts once
   *  somebody has spoken. */
  absentAfterMs?: number;
  /** Clock injection, so stabilization is testable without sleeping. */
  now?: () => number;
}

export interface TeamsCaptions {
  /** Coverage + liveness snapshot; callers surface it, never act on it. */
  health(): TeamsCaptionsHealth;
  /** Run one scan immediately — the poll's body, exposed so a deterministic test
   *  can advance this contract without wall-clock sleeps (same rationale as the
   *  injected clock; `msteams-speakers.ts` exposes `heartbeatMs` for this). */
  scanNow(): void;
  /** Stop watching. Flushes an entry still inside its stabilization window as
   *  `stable:false` — the bridge's stop path is where a meeting's last caption
   *  survives or is lost. */
  destroy(): void;
}

/** Teams re-punctuates and re-cases while refining, so "does this text continue
 * the previous one" is asked on a normalized form: lowercased, punctuation
 * dropped, whitespace collapsed. Either direction counts as continuation —
 * refinement can shorten as well as grow. */
function normalizeForContinuation(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9\s]/g, '').replace(/\s+/g, ' ').trim();
}

function continuesFrom(previous: string, next: string): boolean {
  const a = normalizeForContinuation(previous);
  const b = normalizeForContinuation(next);
  if (!a || !b) return false;
  return b.startsWith(a) || a.startsWith(b);
}

const SEEN_KEYS_MAX = 400;   // bounded: a long meeting must not grow this without limit

export function createTeamsCaptions(opts: TeamsCaptionsOptions): TeamsCaptions {
  const log = opts.log || (() => { /* silent */ });
  const now = opts.now ?? (() => Date.now());
  const stabilizeMs = opts.stabilizeMs ?? 900;
  const pollMs = opts.pollMs ?? 250;
  const absentAfterMs = opts.absentAfterMs ?? 30_000;

  const startedAt = now();
  // 'unknown' — nothing seen yet (the enable step may still be in flight);
  // 'active'  — renderer present; 'lost' — it went away after being active;
  // 'absent'  — it never appeared inside the detection window.
  // The poll never stops in ANY state, so 'lost' and 'absent' are both recoverable.
  let presence: 'unknown' | 'active' | 'lost' | 'absent' = 'unknown';
  let wrapperSelector: string | null = null;
  let authorSelector: string | null = null;
  let textSelector: string | null = null;
  let wrapper: Element | null = null;
  let observer: MutationObserver | null = null;
  let destroyed = false;

  const counters = { authors: 0, texts: 0, emitted: 0, unresolved: 0, self: 0, losses: 0, recoveries: 0 };
  let lastCaptionAtMs: number | null = null;

  // The entry currently being refined. One at a time: Teams appends new entries at
  // the end of the list, so a new pair at the tail means the previous one is final.
  interface Pending { speaker: string; text: string; changedAt: number; emitted: boolean }
  let pending: Pending | null = null;
  const seenKeys = new Set<string>();       // virtualized list re-renders the same rows on scroll
  const seenOrder: string[] = [];
  let lastUnresolvedAt = 0;                 // rate-limit: one unresolved report per stabilize window

  function deliver(observation: TeamsCaptionObservation): void {
    try {
      opts.onObservation?.(observation);
    } catch {
      log(`[TeamsCaptions] observation-delivery-failed type=${observation.type}`);
    }
  }

  function wrapperCandidateCounts(): Array<{ sel: string; count: number }> {
    return teamsCaptionSelectors.wrappers.map((sel) => {
      let count = 0;
      try { count = document.querySelectorAll(sel).length; } catch { count = 0; }
      return { sel, count };
    });
  }

  function findWrapper(): { element: Element; selector: string } | null {
    for (const sel of teamsCaptionSelectors.wrappers) {
      let element: Element | null = null;
      try { element = document.querySelector(sel); } catch { element = null; }
      if (element) return { element, selector: sel };
    }
    return null;
  }

  /** First selector under `root` that matches anything, plus its matches. */
  function firstMatching(root: Element, selectors: string[]): { sel: string; nodes: Element[] } | null {
    for (const sel of selectors) {
      let nodes: Element[] = [];
      try { nodes = Array.prototype.slice.call(root.querySelectorAll(sel)) as Element[]; } catch { nodes = []; }
      if (nodes.length) return { sel, nodes };
    }
    return null;
  }

  function emitCaption(entry: Pending, stable: boolean): void {
    const key = `${entry.speaker}::${entry.text}`;
    if (seenKeys.has(key)) { entry.emitted = true; return; }
    seenKeys.add(key);
    seenOrder.push(key);
    if (seenOrder.length > SEEN_KEYS_MAX) {
      const evicted = seenOrder.shift();
      if (evicted !== undefined) seenKeys.delete(evicted);
    }
    entry.emitted = true;
    counters.emitted++;
    const tMs = now();
    lastCaptionAtMs = tMs;
    log(`💬 [TeamsCaptions] ${stable ? 'CAPTION' : 'CAPTION_PARTIAL'} ${entry.speaker}: ${entry.text.slice(0, 60)}`);
    try {
      opts.onCaption({ speaker: entry.speaker, text: entry.text, tMs, stable });
    } catch {
      log('[TeamsCaptions] caption-delivery-failed');   // a consumer throw never breaks capture
    }
  }

  function emitUnresolved(reason: TeamsCaptionSpeakerUnresolvedObservation['reason']): void {
    const tMs = now();
    if (tMs - lastUnresolvedAt < stabilizeMs) return;   // the poll re-reads the same bad entry ~4×/s
    lastUnresolvedAt = tMs;
    counters.unresolved++;
    deliver({ type: 'caption-speaker-unresolved', platform: 'teams', signal: 'closed-caption', reason, tMs });
    log(`[TeamsCaptions] caption-speaker-unresolved reason=${reason} — entry WITHHELD (no name is invented)`);
  }

  function markActive(selector: string): void {
    if (presence === 'active' && wrapperSelector === selector) return;
    // Coming back after a loss (or after an absent window) is a RECOVERY, not a first activation:
    // the difference is the whole point of tracking a flake-class source.
    const recovered = presence === 'lost' || presence === 'absent';
    if (recovered) counters.recoveries++;
    presence = 'active';
    wrapperSelector = selector;
    deliver({
      type: recovered ? 'captions-recovered' : 'captions-active',
      platform: 'teams',
      signal: 'closed-caption',
      wrapperSelector: selector,
      authorSelector,
      textSelector,
      losses: counters.losses,
      tMs: now(),
    });
    log(
      `[TeamsCaptions] ${recovered ? 'captions-recovered' : 'captions-active'} wrapper=${selector} `
      + `author=${authorSelector ?? '(none yet)'} text=${textSelector ?? '(none yet)'} losses=${counters.losses}`,
    );
  }

  /** The renderer went away after being live. The DOM-outline watcher is untouched
   * and keeps producing hints — this only reports that the SECOND source stopped,
   * so nobody downstream mistakes its silence for agreement. */
  function markLost(): void {
    presence = 'lost';
    counters.losses++;
    // A pending entry can never stabilize once the renderer is gone — flush it.
    if (pending && !pending.emitted) emitCaption(pending, false);
    pending = null;
    deliver({
      type: 'captions-lost',
      platform: 'teams',
      signal: 'closed-caption',
      reason: 'renderer-lost',
      candidates: wrapperCandidateCounts(),
      emitted: counters.emitted,
      tMs: now(),
    });
    log(
      `[TeamsCaptions] captions-lost after ${counters.emitted} caption(s) — the caption source `
      + 'went quiet; the voice-level-outline watcher is unaffected and the poll keeps trying',
    );
  }

  function markAbsent(): void {
    presence = 'absent';
    if (pending && !pending.emitted) emitCaption(pending, false);
    pending = null;
    deliver({
      type: 'captions-absent',
      platform: 'teams',
      signal: 'closed-caption',
      reason: 'renderer-missing',
      candidates: wrapperCandidateCounts(),
      tMs: now(),
    });
    log(
      '[TeamsCaptions] captions-absent reason=renderer-missing — no caption renderer matched; '
      + 'either captions never came on in this meeting or every candidate selector is stale',
    );
  }

  /** Read the LAST author/text pair under the wrapper and advance the stabilizer.
   * Pairing is by document order over the two atom lists (the 0.10 contract): the
   * host view interposes an items-renderer the guest view lacks, so any
   * structural path between wrapper and atoms is host-only and rots. */
  function readAndAdvance(root: Element): void {
    const authors = firstMatching(root, teamsCaptionSelectors.authors);
    const texts = firstMatching(root, teamsCaptionSelectors.texts);
    counters.authors = authors?.nodes.length ?? 0;
    counters.texts = texts?.nodes.length ?? 0;
    if (authors) authorSelector = authors.sel;
    if (texts) textSelector = texts.sel;
    if (!texts || !texts.nodes.length) {
      // Wrapper present, nothing spoken yet (or the text atom was renamed). Not
      // absent — the wrapper only mounts when captions are on.
      maybeStabilize();
      return;
    }
    const lastText = (texts.nodes[texts.nodes.length - 1].textContent || '').trim();
    if (!lastText) { maybeStabilize(); return; }

    if (!authors || !authors.nodes.length) { emitUnresolved('author-empty'); return; }
    const rawSpeaker = (authors.nodes[authors.nodes.length - 1].textContent || '').trim();
    if (!rawSpeaker) { emitUnresolved('author-empty'); return; }
    if (!isTeamsDisplayNameCandidate(rawSpeaker)) { emitUnresolved('author-not-name-shaped'); return; }
    if (isSelfDisplayName(rawSpeaker, opts.selfName)) {
      counters.self++;
      // Our own captions end the previous speaker's entry all the same.
      if (pending && !pending.emitted) emitCaption(pending, true);
      pending = null;
      return;
    }

    const at = now();
    if (!pending) { pending = { speaker: rawSpeaker, text: lastText, changedAt: at, emitted: false }; return; }
    if (pending.speaker !== rawSpeaker) {
      if (!pending.emitted) emitCaption(pending, true);   // superseded ⇒ final
      pending = { speaker: rawSpeaker, text: lastText, changedAt: at, emitted: false };
      return;
    }
    if (pending.text !== lastText) {
      // Same speaker: either this refines the same utterance (prefix-compatible)
      // or Teams replaced the entry with a new sentence — the latter is final.
      if (!continuesFrom(pending.text, lastText) && !pending.emitted) emitCaption(pending, true);
      pending = { speaker: rawSpeaker, text: lastText, changedAt: at, emitted: false };
      return;
    }
    maybeStabilize();
  }

  function maybeStabilize(): void {
    if (!pending || pending.emitted) return;
    if (now() - pending.changedAt >= stabilizeMs) emitCaption(pending, true);
  }

  function scan(): void {
    if (destroyed) return;
    const found = findWrapper();
    if (!found) {
      if (wrapper) { observer?.disconnect(); wrapper = null; }
      if (presence === 'active') markLost();
      else if (presence === 'unknown' && now() - startedAt >= absentAfterMs) markAbsent();
      return;
    }
    if (found.element !== wrapper) {
      wrapper = found.element;
      observer?.disconnect();
      try {
        observer?.observe(wrapper, { childList: true, subtree: true, characterData: true });
      } catch { /* a shimmed/detached node may reject observation — the poll still runs */ }
    }
    // Atoms are read first so the activation observation can name what matched.
    readAndAdvance(found.element);
    markActive(found.selector);
  }

  try {
    observer = new MutationObserver(() => { try { scan(); } catch { /* never break capture */ } });
  } catch {
    observer = null;   // no MutationObserver in this realm — the poll alone still works
  }

  scan();
  const poll = setInterval(() => { try { scan(); } catch { /* never break capture */ } }, pollMs) as unknown as number;

  return {
    health(): TeamsCaptionsHealth {
      return {
        present: presence === 'active',
        losses: counters.losses,
        recoveries: counters.recoveries,
        wrapperSelector,
        authors: counters.authors,
        texts: counters.texts,
        emitted: counters.emitted,
        unresolved: counters.unresolved,
        self: counters.self,
        lastCaptionAtMs,
      };
    },
    scanNow(): void {
      try { scan(); } catch { /* never break capture */ }
    },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      clearInterval(poll);
      observer?.disconnect();
      observer = null;
      wrapper = null;
      // Best-effort tail flush: an entry still refining when the meeting ends is
      // real speech, and waiting for a stabilization that will never come drops it.
      if (pending && !pending.emitted) emitCaption(pending, false);
      pending = null;
      seenKeys.clear();
      seenOrder.length = 0;
    },
  };
}
