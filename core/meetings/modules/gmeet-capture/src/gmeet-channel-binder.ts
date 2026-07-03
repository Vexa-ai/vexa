/**
 * gmeet-channel-binder — correlate a CHANNEL to a TILE (name) by ENERGY ↔ GLOW.
 *
 * The fix for the cross-channel name leak. There is NO structural audio↔participant
 * link in Meet, and the GLOBAL glow ("who is lit anywhere") contaminates every
 * channel. But a channel's AUDIO ENERGY and its speaker's GLOW are driven by the
 * SAME audio — Meet lights tile X exactly when X is loud, and X's audio is what's
 * loud on X's channel. So integrate the agreement over a short window:
 *
 *   channel C ↔ tile X   when C is LOUD exactly while X GLOWS (and not otherwise)
 *
 * This is audio-visual active-speaker correlation. It beats onset-timing because it
 * doesn't need a clean start: even under constant overlap, each channel's energy
 * tracks its OWN speaker's glow (the brief solo moments pull the right tile ahead),
 * so the correlations separate. 1 tile ↔ 1 channel: a tile goes to the channel that
 * correlates with it most. A channel whose best score is below a confidence floor
 * stays UNKNOWN — leak-free, never a guess.
 *
 * Pure logic (no DOM/audio) — golden-testable. The capture composition feeds it glow
 * edges (gmeet-speakers) and a per-frame {channel, energy} (gmeet-capture).
 */
export interface GmeetChannelBinderOptions {
  /** Decay time constant (ms) for the correlation — the effective window. Default 2500. */
  tauMs?: number;
  /** Per-frame energy (peak |sample|) above which a channel counts as actively speaking. Default 0.02. */
  loudThreshold?: number;
  /** Minimum decayed agreement before a channel binds confidently (else UNKNOWN). Default 2.5. */
  minScore?: number;
  /**
   * The LOCAL participant's display name (the host / "self"). The self's own audio is
   * the microphone (a reserved channel), NEVER a remote <audio> element — so the self
   * tile must NEVER bind a remote channel, even when it glows concurrently with a
   * remote speaker. The upstream self-exclusion (gmeet-speakers' data-self-name) is
   * re-read every scan and Meet drops that marker transiently, which let the host's
   * glow leak its name onto a remote channel (speaker-bots eval, 2026-06-20). This is
   * the leak-proof backstop: a sticky self name the binder refuses to assign.
   */
  selfName?: string;
}

interface Score { score: number; ts: number }

export class GmeetChannelBinder {
  private readonly tauMs: number;
  private readonly loudThreshold: number;
  private readonly minScore: number;
  private selfName?: string;
  private readonly glowing = new Set<string>();
  /** channel → (tile name → decayed agreement: how often the channel is LOUD while the tile GLOWS). */
  private readonly agree = new Map<number, Map<string, Score>>();

  constructor(o: GmeetChannelBinderOptions = {}) {
    this.tauMs = o.tauMs ?? 2500;
    this.loudThreshold = o.loudThreshold ?? 0.02;
    this.minScore = o.minScore ?? 2.5;
    if (o.selfName) this.setSelfName(o.selfName);
  }

  /**
   * Declare (or update) the LOCAL participant's name. Sticky: once known, the self can
   * never bind a remote channel even if its data-self-name marker later vanishes. Also
   * PURGES any agreement the self already accrued (it may have leaked before the name
   * was known — the marker can render late), so a prior self-bind collapses to UNKNOWN.
   */
  setSelfName(name?: string): void {
    this.selfName = name;
    if (!name) return;
    this.glowing.delete(name);
    for (const m of this.agree.values()) m.delete(name);
  }

  /** A glow edge for a tile. isEnd=false → onset, true → offset. The self never glows-to-bind. */
  recordGlow(name: string, isEnd: boolean, tsMs: number): void {
    if (name === this.selfName) return;                      // self audio is the mic, never a remote channel
    if (isEnd) this.glowing.delete(name); else this.glowing.add(name);
  }

  /**
   * Update the correlation with this channel's energy at tsMs, and return its current
   * speaker — the glowing tile whose glow best tracks this channel's energy — or
   * undefined (UNKNOWN). `energy` is the frame's peak amplitude (0..1).
   */
  nameForChannel(channel: number, tsMs: number, energy: number): string | undefined {
    if (energy > this.loudThreshold && this.glowing.size > 0) {
      const m = this.agree.get(channel) ?? new Map<string, Score>();
      this.agree.set(channel, m);
      for (const x of this.glowing) {                          // C loud while X glows → +1 evidence C is X
        const e = m.get(x);
        const decayed = e ? e.score * Math.exp(-(tsMs - e.ts) / this.tauMs) : 0;
        m.set(x, { score: decayed + 1, ts: tsMs });
      }
    }
    return this.assign(channel, tsMs);
  }

  private cur(channel: number, name: string, tsMs: number): number {
    const e = this.agree.get(channel)?.get(name);
    return e ? e.score * Math.exp(-(tsMs - e.ts) / this.tauMs) : 0;
  }

  private assign(channel: number, tsMs: number): string | undefined {
    const m = this.agree.get(channel);
    if (!m) return undefined;
    let best: string | undefined;
    let bestScore = 0;
    for (const name of m.keys()) {
      if (name === this.selfName) continue;                  // the self never wins a remote channel
      const s = this.cur(channel, name, tsMs);
      if (s > bestScore) { bestScore = s; best = name; }
    }
    if (best === undefined || bestScore < this.minScore) return undefined;
    // 1 tile ↔ 1 channel: only claim `best` if no OTHER channel correlates with it more strongly.
    for (const other of this.agree.keys()) {
      if (other !== channel && this.cur(other, best, tsMs) > bestScore) return undefined;
    }
    return best;
  }
}
