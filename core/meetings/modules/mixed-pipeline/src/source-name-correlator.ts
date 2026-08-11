/**
 * SourceNameCorrelator — bind an opaque audio SOURCE to a human NAME by co-activity over a call.
 *
 * The same problem appears in two lanes wearing different clothes. On the mixed lane an RTP CSRC is
 * a stable per-meeting integer and the UI says who is lit; on the Google Meet lane an SSRC (or a
 * channel index) is stable and a tile glows. In both cases one signal knows WHEN with precision and
 * nothing about WHO, the other knows WHO and is vague and late about WHEN, and the binding is
 * whichever pairing their activity agrees on across the whole call. Nothing here mentions a
 * platform, a CSRC or a tile: it takes intervals and returns bindings.
 *
 * Three properties, each of which a fixture forced:
 *
 *  • **The lag is scanned, not assumed.** The UI trails the audio, but not by a constant: the
 *    optimum measured over the same code and three real tapes was +1250 ms on two of them and
 *    +2000 ms on the third, against the +1000 ms that had been hard-coded from the first. A
 *    correlator that inherits one meeting's lag will quietly mis-bind another's.
 *
 *  • **Cross-track exclusivity is a HARD constraint, not a tie-break.** On the m30 tape the raw
 *    co-activity picks the SAME name for both sources — 67.8 s for one, 208.6 s for the other —
 *    because one participant's tile was lit for most of the meeting and the UI lag smears it across
 *    the handoffs. Taking each source's own best answer would name both people identically and
 *    erase one of them. A name belongs to at most one source, and it goes to the source holding the
 *    clear majority of THAT NAME's evidence.
 *
 *  • **A margin, and silence below it.** m34's thin case is a 13-point margin between winner and
 *    runner-up on one source. Below the bar the correlator returns nothing for that source, which
 *    is the honest output — the consumer publishes a stable placeholder rather than a guess.
 *
 * `weight` exists for the signal these tapes do not carry: an RTP contributing source reports an
 * audio LEVEL per observation, and weighting co-activity by it would let a loud speaker outvote a
 * source that is merely unmuted. Every tape so far omits it, so it defaults to 1 and is untested
 * against reality — deliberately inert rather than speculatively tuned.
 */

/** A half-open interval [start, end) during which something was true. */
export interface Interval {
  start: number;
  end: number;
  /** Relative importance of this interval, if the sensor supplied one (e.g. an audio level).
   *  Absent ⇒ 1. */
  weight?: number;
}

export interface CorrelatorOptions {
  /** Lags (ms) to try when shifting NAME intervals back onto the source timeline. The winner is the
   *  lag maximising total agreement across every source. */
  lagsMs?: number[];
  /** Share of a source's own co-activity its winner must hold. */
  minShare?: number;
  /** Share of a NAME's total co-activity the winning source must hold — the exclusivity constraint. */
  minOwnerShare?: number;
  /** Percentage points the winner must lead the runner-up by, of the source's own total. */
  minMarginPct?: number;
  /** Total co-activity (ms) a binding needs before it is considered at all. */
  minSupportMs?: number;
}

export interface Binding {
  sourceId: string;
  name: string;
  /** Co-activity, ms, at the chosen lag. */
  supportMs: number;
  /** Winner's share of this SOURCE's co-activity. */
  share: number;
  /** Winner's share of this NAME's co-activity across all sources. */
  ownerShare: number;
  /** Percentage-point lead over the runner-up. */
  marginPct: number;
}

export interface CorrelationResult {
  /** The lag (ms) that maximised agreement — a property of the meeting, not a constant. */
  lagMs: number;
  bindings: Binding[];
  /** Sources that had co-activity but no answer clearing the bars, and why. */
  refused: Array<{ sourceId: string; reason: 'below-share' | 'below-margin' | 'name-taken' | 'below-support' }>;
  /** The full scored matrix at the chosen lag, for a human reading a run afterwards. */
  matrix: Record<string, Record<string, number>>;
}

const DEFAULT_LAGS = [-1000, -500, -250, 0, 250, 500, 750, 1000, 1250, 1500, 2000, 2500, 3000];

/** Weighted overlap of two interval sets, in ms. */
function overlap(a: Interval[], b: Interval[]): number {
  let total = 0;
  for (const x of a) {
    for (const y of b) {
      const lo = Math.max(x.start, y.start);
      const hi = Math.min(x.end, y.end);
      if (hi > lo) total += (hi - lo) * (x.weight ?? 1) * (y.weight ?? 1);
    }
  }
  return total;
}

const shift = (iv: Interval[], by: number): Interval[] =>
  iv.map((x) => ({ start: x.start - by, end: x.end - by, weight: x.weight }));

/**
 * Correlate audio sources against named UI activity.
 *
 * @param sources  sourceId → intervals during which that source was audible (from the transport)
 * @param names    name → intervals during which the UI showed that person active (lit tile, caption)
 */
export function correlateSourcesToNames(
  sources: Record<string, Interval[]>,
  names: Record<string, Interval[]>,
  opts: CorrelatorOptions = {},
): CorrelationResult {
  const lags = opts.lagsMs ?? DEFAULT_LAGS;
  const minShare = opts.minShare ?? 0.6;
  const minOwnerShare = opts.minOwnerShare ?? 0.7;
  const minMarginPct = opts.minMarginPct ?? 10;
  const minSupportMs = opts.minSupportMs ?? 1500;

  const sourceIds = Object.keys(sources);
  const nameKeys = Object.keys(names);
  if (sourceIds.length === 0 || nameKeys.length === 0) {
    return { lagMs: 0, bindings: [], refused: [], matrix: {} };
  }

  // ── 1. Scan the lag. The winner maximises agreement summed over sources: a per-source lag would
  //    let each one pick the shift that flatters its own best guess, which is fitting noise. One
  //    clock skew, one number. ──
  let bestLag = lags[0];
  let bestScore = -1;
  let bestMatrix: Record<string, Record<string, number>> = {};
  for (const lag of lags) {
    const shifted: Record<string, Interval[]> = {};
    for (const n of nameKeys) shifted[n] = shift(names[n], lag);
    const matrix: Record<string, Record<string, number>> = {};
    let score = 0;
    for (const s of sourceIds) {
      matrix[s] = {};
      let best = 0;
      for (const n of nameKeys) {
        const v = overlap(sources[s], shifted[n]);
        matrix[s][n] = v;
        if (v > best) best = v;
      }
      score += best;
    }
    if (score > bestScore) { bestScore = score; bestLag = lag; bestMatrix = matrix; }
  }

  // ── 2. Rank every (source, name) pair once, globally, and assign greedily by strength. Assigning
  //    per source in arbitrary order would let a weak claim take a name a stronger one needed —
  //    which on m30 is the whole difference between naming two people and naming one twice. ──
  const pairs: Array<{ s: string; n: string; v: number }> = [];
  for (const s of sourceIds) for (const n of nameKeys) pairs.push({ s, n, v: bestMatrix[s][n] });
  pairs.sort((a, b) => b.v - a.v);

  const nameTotal: Record<string, number> = {};
  for (const n of nameKeys) nameTotal[n] = sourceIds.reduce((acc, s) => acc + bestMatrix[s][n], 0);
  const sourceTotal: Record<string, number> = {};
  for (const s of sourceIds) sourceTotal[s] = nameKeys.reduce((acc, n) => acc + bestMatrix[s][n], 0);

  const bindings: Binding[] = [];
  const refused: CorrelationResult['refused'] = [];
  const takenName = new Set<string>();
  const boundSource = new Set<string>();

  for (const { s, n, v } of pairs) {
    if (boundSource.has(s)) continue;
    if (v <= 0) continue;
    const rest = nameKeys.filter((x) => x !== n).map((x) => bestMatrix[s][x]);
    const runnerUp = rest.length ? Math.max(...rest) : 0;
    const share = sourceTotal[s] > 0 ? v / sourceTotal[s] : 0;
    const ownerShare = nameTotal[n] > 0 ? v / nameTotal[n] : 0;
    const marginPct = sourceTotal[s] > 0 ? ((v - runnerUp) / sourceTotal[s]) * 100 : 0;

    if (takenName.has(n)) { refused.push({ sourceId: s, reason: 'name-taken' }); continue; }
    if (v < minSupportMs) { refused.push({ sourceId: s, reason: 'below-support' }); boundSource.add(s); continue; }
    if (share < minShare || ownerShare < minOwnerShare) { refused.push({ sourceId: s, reason: 'below-share' }); boundSource.add(s); continue; }
    if (marginPct < minMarginPct) { refused.push({ sourceId: s, reason: 'below-margin' }); boundSource.add(s); continue; }

    bindings.push({ sourceId: s, name: n, supportMs: v, share, ownerShare, marginPct });
    takenName.add(n);
    boundSource.add(s);
  }

  return { lagMs: bestLag, bindings, refused, matrix: bestMatrix };
}
