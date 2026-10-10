# join/src/msteams — Microsoft Teams join flow

Enter a Teams meeting (teams.microsoft.com / teams.live.com) and resolve admission.
`join.ts`, `admission.ts` (roster/lobby oracle), `leave.ts`, `removal.ts`, `selectors.ts`,
`auth-redirect.ts` (origin guard: a meetup-join bounced to the Microsoft sign-in host is a typed
terminal, never an admission timeout), `prejoin-blocked.ts` (pre-join gate: a "Join now" button
Teams keeps disabled, for example because it refused the display name, is a typed terminal, never
a lobby wait).
Imports host symbols from `../_host`, `playwright`, and Node builtins only.

## The pre-join gate (`prejoin-blocked.ts`, #1780)

Teams accepts only letters, numbers, spaces and `- ' . _ @` in a guest's display name. With any
other character it keeps **Join now** disabled. Operator-facing description, limits and the fix:
[Bot participant name](../../../../../../docs/docs/configuration.mdx) and
[Troubleshooting](../../../../../../docs/docs/troubleshooting.mdx).

### How it works

1. `joinMicrosoftTeams` (`join.ts`) opens the meeting link, clicks *Continue on this browser*, and
   waits for the anonymous pre-join (Steps 1–2.5).
2. Step 4 fills the display name. If the name contains characters outside Teams' published rule,
   `teamsDisplayNameDisallowedChars` makes the step log a warning. The warning is a hint only and
   decides nothing.
3. Step 6 finds the visible **Join now** button and calls `assertTeamsJoinNowEnabled`. That reads
   the live button's enabled state for up to 10 s, and dismisses the AV-confirm modal (#467) between
   reads, because that modal can also hold the button disabled.
4. If the button enables, Step 6 clicks it and the flow continues to the AV-confirm handling and
   `waitForTeamsMeetingAdmission` exactly as before.
5. If it stays disabled, the step reads Teams' own validation line from the page (in-page, plain
   string expression) and throws `TeamsPreJoinBlockedError`. The message starts with
   `teams_prejoin_blocked:` and carries the display name, the refused characters and Teams' text.
   The button is never clicked and the admission wait never runs.
6. The error is not an `AdmissionError`. The bot's join driver re-raises it, and the orchestrator's
   join catch emits the terminal `failed` lifecycle event with `completion_reason: join_failure`
   and `reason: String(e)`. meeting-api stores that as the meeting's `last_error`.

**Data ownership.** The join brick only reads the page and throws. The bot orchestrator is the
single writer of the terminal lifecycle event; meeting-api is the single writer of the meeting
row and its `last_error`. Nothing new is stored.

**Contracts and routes.** No contract changes: the terminal travels on the existing sealed
`lifecycle.v1` event, as an existing `CompletionReason` (`join_failure`) with a discriminating
reason text. That is the same idiom as `teams_auth_redirect`.

**Configuration.** None of its own. The display name comes from `BotConfig.botName`, which the
caller sets: `bot_name` on `POST /bots`, a calendar connection's bot name, or `DEFAULT_BOT_NAME`
on meeting-api (`meetingApi.defaultBotName` in Helm). The 10 s budget is a constant in `join.ts`.

### Why it complies

**Architecture** (`docs/docs/governance/architecture.mdx`):

- **P2, couple only through contracts.** The new file imports nothing outside this folder; `join.ts`
  imports it by a relative path inside the module. Gate: `gate:isolation`
  (`node scripts/check-isolation.js`).
- **P6, one front door.** `TeamsPreJoinBlockedError`, `TEAMS_PREJOIN_BLOCKED` and
  `teamsDisplayNameDisallowedChars` are exported from `src/index.ts`; consumers never deep-import.
  Gate: `gate:exports`.
- **P18, fail loud and attributable.** A disabled button was a discarded click error. It is now a
  typed fault (`reasonCode`) with the platform's own text on the lifecycle event. Gate: `gate:node`
  runs `prejoin-blocked.test.ts`.
- **P21, report state from evidence.** `awaiting_admission` is now only reported after a real
  **Join now** click: a button the bot could not click is no longer read as a lobby. Gate:
  `gate:node` (`prejoin-blocked.test.ts`, "the admission wait never ran").
- **No duplication.** It reuses the existing AV-confirm dismissal (`modals.ts`) and the existing
  terminal path of `auth-redirect.ts`. The character rule exists only in `prejoin-blocked.ts`.
  Ungated: reviewed.
- **P12, self-documenting folder.** This README. Gate: `gate:readme`.

**Security.** No new access path, input or route. The check runs inside the bot's own browser
page and reads only the pre-join screen of the meeting it was sent to. The error text carries the
display name the operator chose and Teams' public validation line. It carries no URL, passcode or
query string, so the passcode redaction in `join-passcode.test.ts` still holds. Authorization for
who may send a bot is unchanged and enforced upstream by the gateway and meeting-api.

**Tests** (`prejoin-blocked.test.ts`): a refused name rejects with `TeamsPreJoinBlockedError`, never
clicks the disabled button, never enters the admission wait and reports in under 60 s of virtual
time; a valid name still clicks; a button that enables a moment late is not a refusal; the
character check accepts `- ' . _ @`, spaces and non-ASCII letters, and flags parentheses, colon and
slash. Seven of these assertions fail on the pre-#1780 `join.ts`.
