/**
 * LocalAgreement-N — the confirmation primitive for the mixed transcription engine.
 * As a turn's unconfirmed window is re-submitted to Whisper, only the WORDS that
 * are stable across N consecutive submissions are safe to confirm; the still-
 * forming tail stays pending. We confirm whole leading segments fully inside that
 * stable prefix — never a partial segment, never the trailing forming words.
 *
 * N defaults to 3: live-mixed audio (Teams/Zoom AGC + jitter) makes a 2-pass
 * agreement confirm not-yet-settled text; requiring three identical passes only
 * commits genuinely-stable words. The driver pairs this with a TTL idle-finalize
 * (commit whatever is pending when updates stop) so the stricter threshold never
 * leaves text stuck.
 *
 * Pure + deterministic: no audio, no I/O. The driver owns the buffer, the cut,
 * the turn lifecycle, naming, and publishing; it calls this to decide how many
 * leading segments of one submission may confirm and carries the returned history.
 *
 * ──────────────────────────────────────────────────────────────────────────────
 * WORD-GRANULARITY CONFIRMATION WAS BUILT AND REJECTED — 2026-08-13, iteration 6.
 * ──────────────────────────────────────────────────────────────────────────────
 * The guard below (`prefixLen < currentWords.length`) is contradictory at nsegs = 1: the one
 * segment confirms only if the stable prefix covers all its words, which the guard then forbids.
 * On m26073 (44 s of continuous speech) passes 1–5 all returned one segment, so confirmation was
 * structurally impossible for 12 s. The repair was to confirm at WORD granularity behind a
 * TIME-based forming-tail guard — a word may confirm once its audio ended 1.5 s behind the
 * window's live edge — with the carried history sliced past what confirmed so the surviving tail
 * keeps its agreement credit. It measured worse on every axis and is not shipped.
 *
 * 1. IT IS NOT THE CONSTRAINT ON CONTINUOUS SPEECH. LocalAgreement is a PREFIX rule, so nothing of
 *    any granularity confirms while word 0 disagrees. On m26073 word 0 ran
 *    `Майя · Майя · … · … · Майя · … · … · Мария · Мария · Мария` and first held three passes
 *    running at +24.0 s; the speech it confirmed ended at +6.8 s. The 17.2 s median IS 24.0 − 6.8.
 *    Measured with the repair: 16.7 s, and 67% over budget either way.
 *
 * 2. ON CONVERSATION, AGREEMENT IS THE SLOW PATH AND THE REPAIR MOVES TRAFFIC ONTO IT. Splitting
 *    m26042's confirms by path: close 159 · 891 ms · 1.9% over budget; agreement 19 · 6435 ms ·
 *    94.7% over. Agreement needs `agree` passes at a 2 s tick — a 4 s floor before any guard —
 *    while a close freezes everything the moment a turn ends, and a two-speaker turn ends every
 *    2.5–3.0 s. The repair took agreement from 19 confirms to 154 (m26043: 5 → 110). Neither
 *    path's own latency moved; the mix ratio did, and the aggregate went 1085 → 3942 ms with
 *    over-budget 11.8% → 49.8%. CONVERSATION IS FAST BECAUSE IT CLOSES OFTEN; CONTINUOUS SPEECH IS
 *    SLOW BECAUSE IT CAN ONLY AGREE.
 *
 * 3. THE ADVANCE IS A COMMITMENT, LIKE THE CUT. Advancing to the last confirmed WORD lands
 *    mid-clause, where advancing to a segment end lands at a break the model itself heard. On
 *    m26073 the advance moved 6.78 s → 7.28 s, into «когда все машут руками»; the next window's
 *    model then INVENTED «когда» in two of three passes for audio that no longer contained it, its
 *    word 0 flickered, and it confirmed nothing until close. A word-granularity advance
 *    manufactures the cold left edge that mechanism 1 above is made of.
 *
 * 4. AND IT COST MEANING ON A FIXTURE THAT WAS 0/0. Against the completeness oracle, m26073 lost
 *    the negation in `Дело НЕ в том, ЧЕГО мы с вами ожидали` (shipped as `Дело в том, что мы с
 *    вами ожидали` — the sentence asserts its own opposite), turned `не возражали` into
 *    `не выражали`, dropped `конечно`, and invented a proper name: `Я, Григорий, давай тогда`.
 *    Every counter read 0 findings on that run.
 *
 * A time guard is also a budget line: expressed in the units of the metric it regulates, its value
 * IS that metric's floor. m26042's median lag was 1085 ms — below the 1500 ms the guard installs.
 *
 * Full account: §12 of `~/dev/biz/drafts/2026-08-13-window-mechanics-in-practice.md`.
 */

/** Split into non-empty whitespace-separated words. */
export function words(text: string): string[] {
  return text.trim().split(/\s+/).filter((w) => w.length > 0);
}

/** Length of the longest common leading run of two word arrays. */
export function longestCommonWordPrefix(a: string[], b: string[]): number {
  let n = 0;
  const max = Math.min(a.length, b.length);
  for (let i = 0; i < max; i++) { if (a[i] === b[i]) n = i + 1; else break; }
  return n;
}

/** Length of the leading run identical across ALL of `arrays` (≥1). The heart of
 *  LocalAgreement-N: a word confirms only if every one of the N passes agrees. */
export function commonWordPrefix(arrays: string[][]): number {
  if (arrays.length === 0) return 0;
  const first = arrays[0];
  let n = 0;
  for (let i = 0; i < first.length; i++) {
    const w = first[i];
    let all = true;
    for (const a of arrays) { if (a[i] !== w) { all = false; break; } }
    if (all) n = i + 1; else break;
  }
  return n;
}

export interface AgreementSegment {
  /** Segment text (already gated + window-mapped by the driver). */
  text: string;
  /** Audio-time end (ms) — used to drop segments overrunning the read window. */
  endMs: number;
}

export interface AgreementResult {
  /** Number of leading segments that may confirm this pass. */
  confirmCount: number;
  /** The recent submissions' words to carry into the next pass (`turn.history`,
   *  newest first, capped at `agree-1`). Reset to [] when we advance. */
  history: string[][];
}

/**
 * @param segments  this submission's gated, window-mapped Whisper segments
 * @param history   the previous (agree-1) submissions' words (the turn's `history`)
 * @param spanEndMs the end of the audio window actually read (live edge or boundary)
 * @param closing   on turn close everything confirms (last chance)
 * @param agree     consecutive identical passes required to confirm (default 3)
 */
export function localAgreement(
  segments: AgreementSegment[],
  history: string[][],
  spanEndMs: number,
  closing: boolean,
  agree = 3,
): AgreementResult {
  const currentWords = segments.flatMap((s) => words(s.text));
  if (closing) return { confirmCount: segments.length, history };

  // Need `agree` consecutive submissions (this one + agree-1 carried) before any
  // word can confirm — until then, hold everything pending.
  const passes = [currentWords, ...history];
  const prefixLen = passes.length >= agree ? commonWordPrefix(passes.slice(0, agree)) : 0;

  let confirmCount = 0;
  if (prefixLen > 0 && prefixLen < currentWords.length) {
    let remaining = prefixLen;
    for (const s of segments) {
      const n = words(s.text).length;
      if (remaining >= n) { remaining -= n; confirmCount++; }
      else break; // partial segment — don't emit partial
    }
    // Never confirm past the submitted window: the tail guard already holds the
    // still-forming words; drop any segment whose end overruns the read audio.
    while (confirmCount > 0 && segments[confirmCount - 1].endMs > spanEndMs + 1000) confirmCount--;
  }

  // On advance, reset the history (the tail re-agrees fresh); else carry the last
  // agree-1 submissions (newest first).
  const next = confirmCount > 0 ? [] : [currentWords, ...history].slice(0, agree - 1);
  return { confirmCount, history: next };
}
