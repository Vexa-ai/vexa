/**
 * The Teams pre-join "Join now" gate.
 *
 * Teams validates the anonymous display name on the pre-join screen and keeps "Join now"
 * DISABLED while the name is invalid. The rule, in Teams' own words on that screen:
 *
 *   "Your name can only include letters, numbers, spaces, and these symbols: - ' . _ @"
 *
 * A bot named `Notetaker (recording)` therefore never joins: the button is present, labelled and
 * visible, only disabled. Before this module, the click timed out, the error was discarded, and
 * the admission wait then read that same visible "Join now" button as a waiting-room indicator —
 * so a join that was never attempted was reported as `awaiting_admission` for the whole lobby
 * budget (Vexa-ai/vexa#1780). A host was never asked to admit anything.
 *
 * This module names that condition. `join.ts` asks the live button whether it is enabled after
 * the name is filled; a button that stays disabled is a typed terminal, `TeamsPreJoinBlockedError`
 * (reasonCode `teams_prejoin_blocked`), carrying the validation text Teams showed.
 *
 * The LIVE button is the authority, not the regex below. Teams can change its rule; if it relaxes
 * it, a name this regex dislikes still joins (the regex only adds a log hint). If it tightens it,
 * the disabled button still catches the refusal.
 *
 * Same idiom as `auth-redirect.ts`: deliberately NOT an AdmissionError, so the orchestrator's join
 * catch stamps the message — reasonCode included — onto the terminal event's reason text, where
 * triage reads it.
 */

/** Machine-readable discriminator: Teams kept the pre-join "Join now" button disabled. */
export const TEAMS_PREJOIN_BLOCKED = "teams_prejoin_blocked";

/**
 * Characters Teams accepts in an anonymous display name, per the validation text its pre-join
 * shows: letters, numbers, spaces and `- ' . _ @`. Unicode letters and digits count.
 */
const TEAMS_NAME_ALLOWED = /^[\p{L}\p{N}\p{M} \-'._@]*$/u;
const TEAMS_NAME_ALLOWED_CHAR = /[\p{L}\p{N}\p{M} \-'._@]/u;

/**
 * The characters in `name` that Teams' published display-name rule does not allow, in order of
 * first appearance, de-duplicated. Empty when the name looks acceptable. Advisory only: the
 * enabled state of the live button decides.
 */
export function teamsDisplayNameDisallowedChars(name: string): string[] {
  if (TEAMS_NAME_ALLOWED.test(name)) return [];
  const bad: string[] = [];
  for (const ch of Array.from(name)) {
    if (!TEAMS_NAME_ALLOWED_CHAR.test(ch) && !bad.includes(ch)) bad.push(ch);
  }
  return bad;
}

/** The typed terminal for a Teams pre-join whose "Join now" button never enabled. */
export class TeamsPreJoinBlockedError extends Error {
  readonly reasonCode: typeof TEAMS_PREJOIN_BLOCKED;
  /** The validation text Teams showed on the pre-join, when it showed one. */
  readonly teamsMessage: string | null;
  /** Characters in the display name outside Teams' published rule (advisory). */
  readonly disallowedChars: string[];
  constructor(botName: string, teamsMessage: string | null) {
    const disallowed = teamsDisplayNameDisallowedChars(botName);
    const parts = [
      `${TEAMS_PREJOIN_BLOCKED}: Teams kept the pre-join "Join now" button disabled after the ` +
        `display name was entered, so the bot never asked to join and no host was asked to admit it`,
      `display name ${JSON.stringify(botName)}`,
    ];
    if (disallowed.length) parts.push(`characters Teams does not allow: ${disallowed.join(" ")}`);
    if (teamsMessage) parts.push(`Teams said: ${JSON.stringify(teamsMessage)}`);
    super(parts.join("; "));
    this.name = "TeamsPreJoinBlockedError";
    this.reasonCode = TEAMS_PREJOIN_BLOCKED;
    this.teamsMessage = teamsMessage;
    this.disallowedChars = disallowed;
  }
}
