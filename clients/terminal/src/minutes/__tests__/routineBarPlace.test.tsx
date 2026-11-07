/** The routine strip belongs to the routine's OWN chat: at the top of that conversation, inside the
 *  conversation column — never on the rail, never left over on another chat (founder, app.dev). */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { routineOfOpenChat } from "../RoutineBar";

const ROUTINE = { flow: "weather-lisbon", scope: "user:2", version: 1 };
const CHATS = [
  { id: "flow-0a050cc06321ac04", routine: ROUTINE },
  { id: "chat-1" },
];

describe("which chat carries the routine strip", () => {
  it("the routine's own chat does", () => {
    expect(routineOfOpenChat(CHATS, "flow-0a050cc06321ac04")).toEqual(ROUTINE);
  });
  it("another chat, a new empty chat or no chat does not", () => {
    expect(routineOfOpenChat(CHATS, "chat-1")).toBeUndefined();
    expect(routineOfOpenChat(CHATS, "c-draft-new")).toBeUndefined();
    expect(routineOfOpenChat(CHATS, undefined)).toBeUndefined();
  });
});

describe("where the shell mounts it", () => {
  const src = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
  it("inside the conversation column, above the chat, keyed by the open chat", () => {
    const main = src.indexOf("<main ");
    const bar = src.indexOf("<ChatRoutines");
    const chat = src.indexOf("<Chat params={{ session }}");
    expect(main).toBeGreaterThan(-1);
    expect(bar).toBeGreaterThan(main);
    expect(bar).toBeLessThan(chat);
    expect(src.match(/<ChatRoutines/g)).toHaveLength(1);
    expect(src).toContain("routineOfOpenChat(allChats, session)");
  });
});

import { routineNotice } from "../RoutineBar";

describe("the strip says why a routine did not run", () => {
  it("shows the refusal flows recorded, or why it is paused", () => {
    expect(routineNotice({ enabled: true, last_refusal: { text: "Not run: daily limit of 48 runs reached. The next run can start after 16:30 UTC." } }))
      .toContain("daily limit");
    expect(routineNotice({ enabled: false, paused_reason: "paused by you" })).toBe("Paused — paused by you");
    expect(routineNotice({ enabled: true })).toBeNull();
    expect(routineNotice(undefined)).toBeNull();
  });
});

import { routinesOfChat } from "../RoutineBar";

describe("a chat owns several routines", () => {
  it("lists every routine whose chat it is", () => {
    const cards = [{ name: "prod-check", thread: "meet-86" }, { name: "lisbon-flights", thread: "meet-86" },
                   { name: "weather", thread: "flow-abc" }];
    expect(routinesOfChat(cards, "meet-86")).toEqual(["prod-check", "lisbon-flights"]);
    expect(routinesOfChat(cards, "pchat-x")).toEqual([]);
  });
});

import { concernOf, chatsFromSessions, railRows } from "../chats";

describe("a concern chat's stage", () => {
  it("is read off the session index and carried to the rail row", () => {
    expect(concernOf({ stage: "waiting", key: "meeting_id:m-1" })).toEqual({ stage: "waiting", key: "meeting_id:m-1" });
    expect(concernOf({ stage: "lost" })).toBeUndefined();
    const chats = chatsFromSessions([{ session: "concern-abc", title: "Follow-up", created: 1, last_active: 2,
                                       touched: false, concern: { stage: "opened", key: "meeting_id:m-1" } }], 10);
    expect(railRows(chats, [], 10)[0].concern?.stage).toBe("opened");
  });
});

import { ownershipLine } from "../RoutineBar";

describe("what a chat owns, in words", () => {
  it("says what wakes each routine and where its output goes", () => {
    expect(ownershipLine({ name: "prod-check", trigger: { kind: "schedule", cron: "*/15 * * * *" } }))
      .toBe("⏱ routine prod-check — wakes every 15 min · output: this chat");
    expect(ownershipLine({ name: "follow-up", trigger: { kind: "event", event: "meeting.completed" }, notify: "slack" }))
      .toBe("⇢ event agent follow-up — wakes on meeting.completed · output: this chat and slack");
  });
});
