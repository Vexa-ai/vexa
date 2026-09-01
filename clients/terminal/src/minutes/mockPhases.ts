/** MOCK — three meeting phases, so the LAYOUT can be judged before the data exists.
 *
 *  A meeting is `prep`, `live` or `post` (surfaces/meetingModel.meetingPhase), and each wants a
 *  different room: prep shows the brief you are about to walk in with, live shows words arriving,
 *  post shows what was decided. The shell collapsed all three to `isHeld()` — a two-way test — so
 *  a live meeting and an upcoming one both rendered a bare "Personal page".
 *
 *  Everything here is FAKE and self-contained: three meetings, canned page bodies, and a live
 *  transcript that grows on a timer so "flowing" is visible rather than described. Reached with
 *  `?mock=1`; nothing imports it unless that flag is set. Delete this file to remove the mock.
 */
import type { MeetingMock } from "../surfaces/meetingModel";

export const MOCK_FLAG = "vexa.minutes.mock";

export function mockOn(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (new URLSearchParams(window.location.search).get("mock") === "1") {
      localStorage.setItem(MOCK_FLAG, "1");
      return true;
    }
    return localStorage.getItem(MOCK_FLAG) === "1";
  } catch { return false; }
}

const iso = (mins: number) => new Date(Date.now() + mins * 60000).toISOString();

/** Three rows, one per phase. `live_status` is what meetingPhase() actually reads. */
export const MOCK_MEETINGS: MeetingMock[] = [
  {
    id: -101, title: "Acme — pricing review", status: "past", live_status: "scheduled",
    native_id: "mock-prep", start_time: iso(75),
  } as unknown as MeetingMock,
  {
    id: -102, title: "Standup", status: "live", live_status: "active",
    native_id: "mock-live", start_time: iso(-12),
  } as unknown as MeetingMock,
  {
    id: -103, title: "Blue Light Card — discovery", status: "past", live_status: "completed",
    native_id: "mock-post", start_time: iso(-1500),
  } as unknown as MeetingMock,
];

const BRIEF = `# Acme — pricing review

**In 75 minutes.** Three people, one decision.

## What they want
Volume pricing above 500 seats. Their procurement lead asked twice; we have not answered.

## What we know
- Trialling since June. 41 meetings last month, 12 users.
- They compared us to Recall on price, not on capability.

## The decision you are walking in with
Hold list price and offer annual commit, or discount to close this quarter.

## Open questions
- Who signs? Procurement has not named an owner.
- Is the 500-seat number real or aspirational?
`;

const MINUTES = `# Blue Light Card — discovery

**Held yesterday · 48 minutes · 4 participants**

## Decisions
- They will pilot with the support team first, not engineering.
- Security review happens before any contract talk — their sequence, not ours.

## Owners
- **Them:** Abdul sends the security questionnaire this week.
- **Us:** we answer it inside five working days.

## Open
- Pricing was raised and deliberately deferred.
- No date set for the pilot start.
`;

const TRANSCRIPT_POST = `# Transcript — Blue Light Card — discovery

**14:02** · Abdul: Thanks for making time. I want to be upfront that we cannot talk commercials
until security has been through it.

**14:03** · Dmitry: That is fine. What does that process look like on your side?

**14:03** · Abdul: A questionnaire, then a call if anything is unclear. Usually two weeks.

**14:05** · Dmitry: We can turn the questionnaire around in five working days.

**14:06** · Sarah: Would the pilot be the whole org or one team?

**14:07** · Abdul: Support first. Engineering already has something they like.
`;

/** The live transcript GROWS — the point of the live phase is that words are arriving. */
const LIVE_LINES = [
  "**09:31** · Dmitry: Right, standup. What is blocking?",
  "**09:31** · Jacob: The merge is done. I am on the credential mount now.",
  "**09:32** · Dmitry: Is that the inode thing?",
  "**09:32** · Jacob: Yes — the container pins the old file. Binding the directory fixes it.",
  "**09:33** · Sarah: I can take the transcript writer, nothing produces that file yet.",
  "**09:34** · Dmitry: Do that. It is the thing people click and find empty.",
  "**09:35** · Jacob: One more — minutes mode never shows a live meeting anything.",
];

export function liveTranscript(elapsedMs: number): string {
  const shown = Math.min(LIVE_LINES.length, Math.max(1, Math.floor(elapsedMs / 2500) + 1));
  const body = LIVE_LINES.slice(0, shown).join("\n\n");
  const more = shown < LIVE_LINES.length ? "\n\n_…listening_" : "\n\n_— the bot is still in the room —_";
  return `# Standup — live\n\n${body}${more}`;
}

/** The live room's SECOND page. A meeting that is running still has the brief you walked in with —
 *  and without a body here the live room advertised a "Brief" chip that opened "No page here yet". */
const LIVE_BRIEF = `# Standup

**Running now.** Daily, 15 minutes, three people.

## What you walked in with
- The credential mount is still pinned to a stale inode — Jacob owns it.
- Nothing writes the transcript file yet, so every live room opens empty.

## Watch for
Anything that turns into a commitment. Say it out loud and it lands in the minutes.
`;

/** Canned bodies by path. `null` = not a mock path; the real fetch runs. */
export function mockBody(path: string, elapsedMs: number): string | null {
  if (path === "kg/entities/meeting/mock-prep.md") return BRIEF;
  if (path === "kg/entities/meeting/mock-post.md") return MINUTES;
  if (path === "kg/entities/meeting/mock-post.transcript.md") return TRANSCRIPT_POST;
  if (path === "kg/entities/meeting/mock-live.md") return LIVE_BRIEF;
  if (path === "kg/entities/meeting/mock-live.transcript.md") return liveTranscript(elapsedMs);
  if (path === "README.md") return "# Personal\n\nYour own page. Meetings you are in write here.\n";
  return null;
}
