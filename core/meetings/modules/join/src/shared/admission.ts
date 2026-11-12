/**
 * Typed admission outcome shared by every platform admission wait (google_meet, jitsi, …).
 *
 * denial               — a moderator/host explicitly rejected the bot from the lobby/waiting room.
 * lobby_timeout        — the bot stayed in the lobby/waiting state past the admission timeout.
 * join_failure         — the bot never reached the lobby / no admission signal ever appeared.
 * auth_session_missing — authenticated mode found a signed-out browser profile (the guest lobby
 *                        rendered); the profile is dead, so a re-spawn can never succeed.
 *
 * The JoinDriver maps this `outcome` onto a completion reason, so a lobby timeout is reported
 * as `awaiting_admission_timeout` (retry-honest) rather than a generic `join_failure`. Lives in
 * `shared/` so each platform throws the SAME class the driver checks with `instanceof`, without
 * coupling one platform sibling to another.
 */
export type AdmissionOutcome = "denial" | "lobby_timeout" | "join_failure" | "auth_session_missing";

/** What the platform itself returned when it refused (D367, card brain:card/9095fe9ee7c0): one shape for
 *  every platform. `call`/`status`/`body` are the refusing network answer; `page_text` is the text of the
 *  refusal element only, never the whole page (a lobby page can name the people in the call). Raw here,
 *  capped; meeting-api redacts before anything is stored. */
export interface PlatformResponse {
  call?: string;
  status?: number;
  body?: string;
  page_text?: string;
}
/** The most of any one platform-response field carried out of the browser. */
export const PLATFORM_RESPONSE_MAX_CHARS = 2000;

export class AdmissionError extends Error {
  readonly outcome: AdmissionOutcome;
  /** What the platform returned, when the refusal was observed with one. */
  platformResponse?: PlatformResponse;
  constructor(outcome: AdmissionOutcome, message: string) {
    super(message);
    this.name = "AdmissionError";
    this.outcome = outcome;
  }
}
