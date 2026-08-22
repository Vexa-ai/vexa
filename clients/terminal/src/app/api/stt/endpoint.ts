/** The one URL rule for `TRANSCRIPTION_SERVICE_URL`, shared by every consumer.
 *
 *  The env var is accepted in BOTH shapes an operator naturally writes: a bare base
 *  (`https://api.openai.com`) and the full endpoint (`https://api.openai.com/v1/audio/transcriptions`).
 *  The path is appended only when it is not already there — appending blindly double-paths the full
 *  shape into a 404, so the same URL that transcribes a meeting would fail dictation.
 *
 *  Same rule as `@vexa/transcribe-whisper`'s TranscriptionClient (the canonical contract) and the
 *  config.v1 boot probe (`deploy/contracts/config.v1/preflight.py:probe_url`).
 */
export const STT_PATH = "/v1/audio/transcriptions";

export function sttEndpoint(configuredUrl: string): string {
  const base = (configuredUrl ?? "").trim().replace(/\/+$/, "");
  if (!base) return "";
  if (base.endsWith(STT_PATH)) return base;
  // A base that already carries the /v1 prefix (e.g. https://api.groq.com/openai/v1)
  // would double-path into /v1/v1/... — strip the overlap before appending.
  const overlap = "/v1";
  if (base.endsWith(overlap) && STT_PATH.startsWith(overlap + "/")) {
    return base + STT_PATH.slice(overlap.length);
  }
  return `${base}${STT_PATH}`;
}
