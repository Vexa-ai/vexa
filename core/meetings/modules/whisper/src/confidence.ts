/**
 * Low-confidence / hallucinated STT-segment filter — applied at the stt.v1 egress
 * (the shared transcription client), the single chokepoint for every lane. Drops
 * faster-whisper segments that are acoustically junk before they ever reach the
 * confirm loop. (The phrase-list hallucination filter is a separate, downstream
 * concern that lives with the buffer.)
 */
export function isLowConfidenceSegment(s: { avg_logprob?: number; no_speech_prob?: number; compression_ratio?: number }): boolean {
  if (s.no_speech_prob !== undefined && s.avg_logprob !== undefined && s.no_speech_prob > 0.6 && s.avg_logprob < -1.0) return true;
  if (s.compression_ratio !== undefined && s.compression_ratio > 2.4) return true;
  if (s.avg_logprob !== undefined && s.avg_logprob < -1.3) return true;
  return false;
}
