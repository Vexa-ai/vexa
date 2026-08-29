/**
 * Typed admission outcome shared by every platform admission wait (google_meet, jitsi, …).
 *
 * denial               — a moderator/host explicitly rejected the bot from the lobby/waiting room.
 * lobby_timeout        — the bot stayed in the lobby/waiting state past the admission timeout.
 * join_failure         — the bot never reached the lobby / no admission signal ever appeared.
 * meeting_not_found    — the platform answered that the meeting SPACE does not exist (a dead,
 *                        revoked or mistyped code). There is no lobby to reach and no CTA to
 *                        click, so waiting cannot help and a re-spawn can never succeed.
 * auth_session_missing — authenticated mode found a signed-out browser profile (the guest lobby
 *                        rendered); the profile is dead, so a re-spawn can never succeed.
 *
 * The JoinDriver maps this `outcome` onto a completion reason, so a lobby timeout is reported
 * as `awaiting_admission_timeout` (retry-honest) rather than a generic `join_failure`. Lives in
 * `shared/` so each platform throws the SAME class the driver checks with `instanceof`, without
 * coupling one platform sibling to another.
 */
export type AdmissionOutcome =
  | "denial"
  | "lobby_timeout"
  | "join_failure"
  | "auth_session_missing"
  | "meeting_not_found";

export class AdmissionError extends Error {
  readonly outcome: AdmissionOutcome;
  constructor(outcome: AdmissionOutcome, message: string) {
    super(message);
    this.name = "AdmissionError";
    this.outcome = outcome;
  }
}
