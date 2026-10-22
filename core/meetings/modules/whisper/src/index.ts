/**
 * @vexa/transcribe-whisper — the shared stt.v1 egress.
 *
 * One concern: take a PCM window → call the hosted Whisper service → return
 * verbose segments, with the low-confidence/hallucination STT filter applied at
 * source. Both lanes (gmeet channel-router, mixed segmenter) drive the shared
 * buffer, which calls THIS via the injected `transcribe(pcm, prompt)` fn — so
 * whisper has no knowledge of topology, naming, or confirmation.
 */
export {
  TranscriptionClient, TranscriptionError, DEFAULT_STT_REQUEST_TIMEOUT_MS,
  DEFAULT_STT_MIN_REQUEST_TIMEOUT_MS, DEFAULT_STT_TIMEOUT_PER_AUDIO_SEC, sttRequestTimeoutMs,
} from './transcription-client.js';
export type {
  TranscriptionWord,
  TranscriptionSegment,
  TranscriptionResult,
  TranscriptionClientConfig,
  TranscriptionFaultKind,
} from './transcription-client.js';
export { SttGate, parseRetryAfter, bodySaysOverloaded } from './stt-gate.js';
export type { SttPressure, SttGateConfig } from './stt-gate.js';
export { isLowConfidenceSegment, confidenceFilterFromEnv, STRICT_MIN_AVG_LOGPROB, STRICT_MIN_AUDIO_SEC } from './confidence.js';
export type { ConfidenceFilterMode } from './confidence.js';
export { SsrfError, isBlockedIp, isBlockedHostname, validateUrl, guardedLookup, literalAddress, resolveHost,
  isNeverAllowedIp, parseAllowance, allowanceAdmitsHost, allowanceAdmitsIp } from './url-guard.js';
export type { OperatorAllowance, Resolver } from './url-guard.js';
export { setLogger } from './log.js';
