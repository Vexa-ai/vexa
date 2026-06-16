/**
 * @vexa/transcribe-buffer — the shared confirmation core.
 *
 * The LocalAgreement-N confirm primitive the mixed transcription engine drives: a
 * turn's unconfirmed window is re-submitted to Whisper, and only words stable
 * across N (default 3) consecutive passes confirm. The driver owns the buffer/cut/
 * turn-lifecycle and calls this to decide what may confirm, carrying the returned
 * history; it pairs the threshold with a TTL idle-finalize so stricter agreement
 * never strands pending text.
 *
 * Pure + deterministic; pinned by the confirm-loop golden.
 */
export { localAgreement, words, longestCommonWordPrefix, commonWordPrefix } from './local-agreement';
export type { AgreementSegment, AgreementResult } from './local-agreement';
