/**
 * Low-confidence / hallucinated STT-segment filter — applied at the stt.v1 egress
 * (the shared transcription client), the single chokepoint for every lane. Drops
 * faster-whisper segments that are acoustically junk before they ever reach the
 * confirm loop. (The phrase-list hallucination filter is a separate, downstream
 * concern that lives with the buffer.)
 *
 * Two modes:
 *   'off' (default) — the always-on rule below.
 *   'strict'        — also drops a segment whose window Whisper decoded with an average
 *                     log-probability under STRICT_MIN_AVG_LOGPROB. Measured 2026-10-14 on 10 s
 *                     windows of real recordings that passed the lane's speech gate (bbb GPU unit,
 *                     large-v3-turbo, server VAD on): it removed every invented segment found in
 *                     English and Russian windows (8/8) and 7 of 16 in windows Whisper assigned a
 *                     wrong language, while losing 0.07% of English and 0.66% of Russian words
 *                     (short, mumbled phrases). On that route no_speech_prob is always 0 and
 *                     compression_ratio separates only loops, so neither is used beyond the base rule.
 */
export type ConfidenceFilterMode = 'off' | 'strict';

/** Strict mode applies only to requests carrying at least this much audio (s). It was measured on
 *  10 s windows; on the sub-second to few-second windows of pause finals real speech scores lower
 *  (app.dev 2026-10-15: 62 drops in 9 min of a live call), so shorter requests keep the base rule. */
export const STRICT_MIN_AUDIO_SEC = 8;

/** Strict mode: a window decoded below this average log-probability is not trusted. */
export const STRICT_MIN_AVG_LOGPROB = -1.0;

export function isLowConfidenceSegment(
  s: { avg_logprob?: number; no_speech_prob?: number; compression_ratio?: number },
  mode: ConfidenceFilterMode = 'off',
): boolean {
  if (s.no_speech_prob !== undefined && s.avg_logprob !== undefined && s.no_speech_prob > 0.6 && s.avg_logprob < -1.0) return true;
  if (s.compression_ratio !== undefined && s.compression_ratio > 2.4) return true;
  if (s.avg_logprob !== undefined && s.avg_logprob < -1.3) return true;
  if (mode === 'strict' && s.avg_logprob !== undefined && s.avg_logprob < STRICT_MIN_AVG_LOGPROB) return true;
  return false;
}

/** BOT_STT_CONFIDENCE_FILTER → mode: only "strict" turns the stricter rule on. */
export function confidenceFilterFromEnv(value: string | undefined): ConfidenceFilterMode {
  return (value ?? '').trim().toLowerCase() === 'strict' ? 'strict' : 'off';
}
