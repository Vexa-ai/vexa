/** A CHAT THAT CREATES A MEETING BECOMES THAT MEETING'S CHAT (Vexa-ai/vexa#1597).
 *
 *  Founder, 2026-09-06, in a live Google Meet he had started FROM a chat — the chat sent the bot,
 *  the transcript canvas opened beside it, *"which is fantastic"* — and then:
 *
 *      *"i seem to have closed the transcript and now can't find one, if chat is a specific meeting
 *      — and that's a chat feature that it gets after creating meeting from itself — this transcript
 *      should be pinned. and the chat itself should be Live (left sidebar), while there is no need
 *      to create a new chat for that — we already have meeting owner, just attach the status to it"*
 *
 *  Two defects, one cause. His rail carried TWO rows for one meeting — the conversation that sent
 *  the bot, and an auto-created `Google Meet · cqb-egsq… live` row beside it — and the conversation
 *  wore none of a meeting chat's furniture: no pinned transcript, no chips, so a closed tab was a
 *  transcript with no way back to it. The cause is one absent fact: the chat did not carry the
 *  meeting's ref, because the only way this client ever learned one was reading it off a
 *  `meet-<row>` session id, which a chat that MAKES a meeting does not have.
 *
 *  So the binding is the fix and everything here is a consumer of it. The server half — where the
 *  binding is written so it outlives this browser — is pinned in
 *  `core/agent/tests/test_chat_meeting_binding.py`.
 *
 *  ⚠ THE CHIPS HALF OF THIS FILE IS GONE (Vexa-ai/vexa#1600). Shown the "Open transcript" chip that
 *  gave him the way back, the founder ruled on the cause instead — *"just keep a tab that can't be
 *  closed instead"* — so a meeting chat's pages are now unclosable and there is no way back to
 *  offer. What remains here is the binding; the successor rule is `permanentTab.test.tsx`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import {
  bindMeeting, chatsFromSessions, mergeChats, railRows, visibleRows,
  type Artifact, type Chat, type ServerSession,
} from "../chats";
import { Rail } from "../Rail";
import { boundMeetingView, meetingPages, pageForArtifact, withMeetingPages, withoutSeparateTranscript } from "../roomView";
import type { MeetingMock } from "../../surfaces/meetingModel";

const NOW = Date.UTC(2026, 8, 6, 12, 0, 0);
const at = (mins: number) => new Date(NOW + mins * 60000).toISOString();

const meeting = (id: string, title: string, live_status: string, startMins: number): MeetingMock =>
  ({ id, title, status: live_status === "active" ? "live" : "past", live_status,
     native_id: `n-${id}`, start_time: at(startMins) } as unknown as MeetingMock);

/** The meeting his chat made: the bot is in the room. */
const LIVE = meeting("118", "Google Meet · cqb-egsq-vmt", "active", -3);
/** …the same meeting, an hour later. */
const HELD = meeting("118", "Google Meet · cqb-egsq-vmt", "completed", -90);
/** A meeting nobody has chatted about — a mailbox invite. */
const OTHERS = meeting("42", "DNA TSC", "completed", -1500);

const chat = (over: Partial<Chat> & { id: string }): Chat => ({
  label: "send a bot to https://meet.google.com/cqb-egsq-vmt",
  workspaces: ["personal", "_global"], artifacts: [], touched: true,
  createdAt: NOW - 60000, lastActivityAt: NOW - 1000, ...over,
});

const session = (over: Partial<ServerSession> & { session: string }): ServerSession => ({
  title: null, created: Date.UTC(2026, 8, 6, 11, 0, 0) / 1000,
  last_active: Date.UTC(2026, 8, 6, 11, 30, 0) / 1000,
  workspaces: null, scaffold: null, touched: true, meeting: null, ...over,
});

// ── what the shell binds FROM ────────────────────────────────────────────────────────────────────

describe("the send's own event names the meeting to bind", () => {
  /** The `artifact` a successful `bot_send` earns (`llm/claude_code.py::_bot_artifact`), as the
   *  chat stream hands it to the shell. The shell resolves it with the ONE resolver every panel
   *  route already uses and binds when the answer is a meeting — so the dialect and the binding
   *  cannot drift apart without this failing. */
  it("resolves to the meeting canvas, by row", () => {
    expect(pageForArtifact({ workspace: "", path: "meeting:118" }))
      .toEqual({ kind: "meeting", path: "118", label: "Transcript" });
  });

  it("…and a file the turn wrote names no meeting, so nothing binds", () => {
    expect(pageForArtifact({ workspace: "", path: "kg/entities/person/ada.md" })?.kind).toBeUndefined();
    expect(pageForArtifact({ path: "meeting:" })).toBeNull();
  });
});

// ── the binding itself ───────────────────────────────────────────────────────────────────────────

describe("bindMeeting — the chat takes the meeting it made", () => {
  it("puts the ref on THAT chat and leaves every other alone", () => {
    const before = [chat({ id: "pchat-abc" }), chat({ id: "pchat-other", label: "Plan the launch" })];
    const after = bindMeeting(before, "pchat-abc", "118");
    expect(after[0].meeting).toBe("118");
    expect(after[1].meeting).toBeUndefined();
    // …and it is still the SAME conversation: the name the person's own first sentence gave it
    // stands. He asked for the meeting's status on this row, not for the row to become a meeting.
    expect(after[0].label).toBe("send a bot to https://meet.google.com/cqb-egsq-vmt");
  });

  it("LATCHES — a second send does not move the room out from under the reader", () => {
    const bound = bindMeeting([chat({ id: "pchat-abc" })], "pchat-abc", "118");
    expect(bindMeeting(bound, "pchat-abc", "119")[0].meeting).toBe("118");
  });

  it("changes nothing, by identity, when there is nothing to change", () => {
    const before = [chat({ id: "pchat-abc" })];
    expect(bindMeeting(before, "pchat-abc", "")).toBe(before);        // no meeting named
    expect(bindMeeting(before, "pchat-gone", "118")).toBe(before);    // no such chat (a draft)
    const bound = bindMeeting(before, "pchat-abc", "118");
    expect(bindMeeting(bound, "pchat-abc", "118")).toBe(bound);       // already bound
  });
});

describe("the binding comes back from the server, for a chat whose id says nothing", () => {
  it("reads `meeting` off the session row", () => {
    const [c] = chatsFromSessions([session({ session: "pchat-abc", title: "send a bot", meeting: "118" })], NOW);
    expect(c.meeting).toBe("118");
    // its own name survives — only a chat BORN as a meeting's is named by its meeting
    expect(c.label).toBe("send a bot");
  });

  it("still prefers the id when the id names one — one convention, one owner (#1591)", () => {
    const [c] = chatsFromSessions([session({ session: "meet-42", title: "whatever", meeting: "999" })], NOW);
    expect(c.meeting).toBe("42");
    expect(c.label).toBe("");
  });

  it("null, absent or blank is simply no binding", () => {
    for (const m of [null, undefined, "  "] as (string | null | undefined)[]) {
      expect(chatsFromSessions([session({ session: "pchat-abc", meeting: m })], NOW)[0].meeting).toBeUndefined();
    }
  });

  it("a binding this browser just made is not undone by an index that has not caught up", () => {
    const local = bindMeeting([chat({ id: "pchat-abc" })], "pchat-abc", "118");
    const behind = chatsFromSessions([session({ session: "pchat-abc" })], NOW);
    expect(mergeChats(local, behind)[0].meeting).toBe("118");
    // …and the other direction: a second window has never heard of the send
    expect(mergeChats([chat({ id: "pchat-abc" })],
      chatsFromSessions([session({ session: "pchat-abc", meeting: "118" })], NOW))[0].meeting).toBe("118");
  });

  it("a chat still called 'New chat' takes the real title the server holds for it", () => {
    // this used to be barred: the merge kept the local label whenever either side named a meeting,
    // a rule whose premise ("a meeting chat has no label") stopped being true here.
    const local = [chat({ id: "pchat-abc", label: "New chat", meeting: "118" })];
    const server = chatsFromSessions([session({ session: "pchat-abc", title: "send a bot", meeting: "118" })], NOW);
    expect(mergeChats(local, server)[0].label).toBe("send a bot");
  });
});

// ── the rail: one row, and the status on it ──────────────────────────────────────────────────────

describe("railRows — the meeting is a status on the chat, not a second row", () => {
  const bound = chat({ id: "pchat-abc", meeting: "118" });

  it("shows ONE row for a meeting its chat owns, and marks it live", () => {
    const rows = railRows([bound], [LIVE], NOW);
    expect(rows).toHaveLength(1);
    expect(rows[0].chatId).toBe("pchat-abc");
    expect(rows[0].meetingId).toBe("118");
    expect(rows[0].live).toBe(true);
    expect(rows[0].status).toBe("live");
    expect(rows[0].whenLabel).toBe("live");
    // the row is the conversation, named by the person who started it
    expect(rows[0].label).toBe("send a bot to https://meet.google.com/cqb-egsq-vmt");
  });

  it("…and it is the founder's own before-and-after: unbound, the same data is TWO rows", () => {
    // his screenshot — the conversation, and an auto-created `Google Meet · cqb-egsq… live` row
    // lifted above it, for the meeting that conversation had just made
    const rows = railRows([chat({ id: "pchat-abc" })], [LIVE], NOW);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.chatId).sort()).toEqual([null, "pchat-abc"]);
  });

  it("says `held` once the meeting is over, and keeps saying WHEN it was", () => {
    const rows = railRows([bound], [HELD], NOW);
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("held");
    expect(rows[0].live).toBe(false);
    expect(rows[0].whenLabel).not.toBe("held");   // the time is not the status and never replaces it
  });

  it("a chat about no meeting has no status at all", () => {
    expect(railRows([chat({ id: "pchat-plain" })], [], NOW)[0].status).toBeNull();
  });

  it("an upcoming meeting keeps its clock instead of a status word", () => {
    const soon = meeting("77", "Acme — pricing", "scheduled", 90);
    expect(railRows([chat({ id: "c", meeting: "77" })], [soon], NOW)[0].status).toBeNull();
  });

  it("a meeting nobody chatted about still lists — and still becomes a chat when opened", () => {
    const rows = railRows([bound], [LIVE, OTHERS], NOW);
    expect(rows).toHaveLength(2);
    const derived = rows.find((r) => r.meetingId === "42")!;
    expect(derived.chatId).toBeNull();     // no chat yet; `chatForRow` mints one on open
    expect(derived.label).toBe("DNA TSC");
    expect(derived.status).toBe("held");
  });

  it("the live chat sits at the top and survives the default filter", () => {
    const rows = railRows([chat({ id: "old", label: "Old", meeting: undefined }), bound], [LIVE, OTHERS], NOW);
    expect(rows[0].chatId).toBe("pchat-abc");
    expect(visibleRows(rows, false).map((r) => r.meetingId)).toContain("118");
  });
});

describe("the rail renders the status the row carries", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  const rail = (meetings: MeetingMock[]) => render(
    <Rail rows={railRows([chat({ id: "pchat-abc", meeting: "118" })], meetings, NOW)}
      hidden={0} all onAll={() => {}} selKey={null} onSelect={() => {}}
      onNewChat={() => {}} onDeleteChat={() => {}} />,
  );

  it("marks a finished meeting's chat `held`", () => {
    expect(rail([HELD]).container.querySelector('[data-row-status="held"]')?.textContent).toBe("held");
  });

  it("does not say `live` twice — the accent word where the time goes already does", () => {
    const { container } = rail([LIVE]);
    expect(container.querySelector("[data-row-status]")).toBeNull();
    expect(container.textContent).toContain("live");
  });
});

// ── the furniture a bound chat gets ──────────────────────────────────────────────────────────────

const desk: Artifact = { path: "README.md", label: "Desk", desk: true };
const reading: Artifact = { path: "drafts/brief.md", label: "brief", at: 5 };
const NOTE = "kg/entities/meeting/2026-09-06-1200-google-meet.md";

describe("meetingPages — what binding ADDS, which is not a room", () => {
  // `permanent` is stamped here and only here (Vexa-ai/vexa#1600) — these pages are the MEETING's,
  // so they carry no close control and every close path refuses them. See `permanentTab.test.tsx`.
  it("is the transcript and the meeting's document, and nothing else", () => {
    expect(meetingPages("live", "118", NOTE)).toEqual([
      { kind: "meeting", path: "118", label: "Transcript", permanent: true },
      { path: NOTE, label: "Brief", permanent: true },
    ]);
    // no personal page: the chat already has a home in its strip, and a tab nobody asked for is
    // exactly what the Obsidian ruling (#1585) removed.
  });

  it("degrades to the transcript alone before a report exists — which is every fresh send", () => {
    expect(meetingPages("live", "118", null))
      .toEqual([{ kind: "meeting", path: "118", label: "Transcript", permanent: true }]);
  });

  it("names the document for the phase it is read in", () => {
    expect(meetingPages("post", "118", NOTE)[1].label).toBe("Minutes");
    expect(meetingPages("prep", "118", NOTE)).toEqual([{ path: NOTE, label: "Brief", permanent: true }]);
  });
});

describe("withMeetingPages — the transcript arrives PINNED, behind the reader", () => {
  it("adds the meeting's pages as pins and leaves the reader's page where it was", () => {
    const out = withMeetingPages([desk, reading], meetingPages("live", "118", NOTE));
    const byPath = Object.fromEntries(out.map((a) => [a.path, a]));
    expect(byPath["118"].pinned).toBe(true);
    expect(byPath[NOTE].pinned).toBe(true);
    // the page they were reading is untouched — still there, still the one preview slot
    expect(byPath["drafts/brief.md"]).toEqual(reading);
    // …and the pins sit left of it, so the single preview slot cannot evict them
    expect(out.map((a) => a.path)).toEqual(["README.md", "118", NOTE, "drafts/brief.md"]);
  });

  it("never duplicates, and never pins the desk", () => {
    const already: Artifact = { kind: "meeting", path: "118", label: "Transcript", at: 9 };
    const out = withMeetingPages([desk, already], meetingPages("live", "118", null));
    expect(out.filter((a) => a.kind === "meeting")).toHaveLength(1);
    expect(out.find((a) => a.kind === "meeting")!.pinned).toBe(true);
    // the desk is a product default, not something the reader put there (`homeEntry`)
    expect(out.find((a) => a.desk)!.pinned).toBeUndefined();
  });

  it("is idempotent — binding twice is binding once", () => {
    const room = meetingPages("live", "118", NOTE);
    const once = withMeetingPages([desk, reading], room);
    expect(withMeetingPages(once, room)).toEqual(once);
  });
});

// ── the page the send just minted (Vexa-ai/vexa#1601) ───────────────────────────────────────────
//
//  Founder, 2026-09-06 12:50Z, in the same live Meet: *"where is it?"* — the meeting doc. There was
//  none. It was written when the call ENDED, so #1598's one-page room had no page to be, and the
//  binding above could only ever pin a transcript. agent-api mints it on the send now, before it
//  relays the event this chat binds off, so by the time the client asks `/api/meeting/note` the
//  answer is a real page — and when that page declares the widget it IS the room.

describe("boundMeetingView — the minted page is what the send opens", () => {
  const withWidget = { path: NOTE, transcript: "118" };

  it("fronts the meeting's own page when that page carries the transcript", () => {
    const room = meetingPages("live", "118", NOTE, null, { noteHasTranscript: true });
    // ONE page — and it is the meeting's, so it wears `permanent` (#1600) like every page this
    // composer marks as the room's own
    expect(room).toEqual([{ path: NOTE, label: "Brief", permanent: true }]);
    expect(boundMeetingView(room, withWidget)).toBe(room[0]);
  });

  it("fronts NOTHING for a report written before the widget existed", () => {
    // those meetings keep the two-page room they have today; fronting a static report over a live
    // transcript would be the regression `noteHasTranscript` exists to prevent
    const room = meetingPages("live", "118", NOTE, null, { noteHasTranscript: false });
    expect(room.map((p) => p.label)).toEqual(["Transcript", "Brief"]);
    expect(boundMeetingView(room, { path: NOTE, transcript: "" })).toBeNull();
  });

  it("fronts nothing when there is no page yet — one document fewer, never a tab onto a guess", () => {
    expect(boundMeetingView(meetingPages("live", "118", null), { path: null, transcript: "" })).toBeNull();
  });

  it("never fronts the transcript canvas, whatever the note says", () => {
    const room = meetingPages("live", "118", NOTE);
    expect(boundMeetingView(room, { path: "118", transcript: "118" })).toBeNull();
  });
});

describe("withoutSeparateTranscript — one page, not two", () => {
  it("drops the tab the send pinned once the page carries the widget", () => {
    const room = meetingPages("live", "118", NOTE, null, { noteHasTranscript: true });
    // the strip as the artifact event left it: the transcript canvas, pinned and in front
    const pinnedByTheSend: Artifact = { kind: "meeting", path: "118", label: "Transcript", pinned: true };
    const merged = withMeetingPages([desk, pinnedByTheSend], room);
    const out = withoutSeparateTranscript(merged, "118");
    expect(out.map((a) => a.path)).toEqual(["README.md", NOTE]);
    expect(out.find((a) => a.path === NOTE)!.pinned).toBe(true);
  });

  it("removes ONE identity and leaves everything else — another meeting's tab included", () => {
    const other: Artifact = { kind: "meeting", path: "42", label: "Transcript" };
    const out = withoutSeparateTranscript([desk, other, reading], "118");
    expect(out).toEqual([desk, other, reading]);
  });

  it("never removes the desk", () => {
    const homeIsAMeeting: Artifact = { kind: "meeting", path: "118", label: "Desk", desk: true };
    expect(withoutSeparateTranscript([homeIsAMeeting], "118")).toEqual([homeIsAMeeting]);
  });
});

// ── the way back to a transcript you closed ──────────────────────────────────────────────────────
//
//  There is none, because there is no way to close one (Vexa-ai/vexa#1600). The `openChips` suite
//  that stood here — the standing "Open transcript" · "Open note" row #1586 added for exactly this
//  state — went with the chips themselves. Its successor is `permanentTab.test.tsx`, which pins the
//  stronger rule: the meeting's pages carry no `×`, every close path refuses them, and a strip
//  stored before the ruling (including one whose transcript WAS closed) is repaired on open.
