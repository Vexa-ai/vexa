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
