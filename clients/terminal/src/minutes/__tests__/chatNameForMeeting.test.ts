/** The chat bound to a meeting names it when the meeting has no title (UI decisions 14). */
import { afterEach, describe, expect, it } from "vitest";
import { CHATS_KEY, chatNameForMeeting } from "../chats";
import { meetingName } from "../../canvas/MeetingViewHeader";
import type { MeetingMock } from "../../surfaces/meetingModel";

const row = (o: Partial<MeetingMock> = {}) => ({
  id: "119", title: "Google Meet · how-iich-wse", native_id: "how-iich-wse", platform: "google_meet", when: "", status: "past",
  live_status: "completed", participants: [], mentioned: [], actions: [], transcript: [], insights: [], ...o,
}) as MeetingMock;

afterEach(() => { localStorage.removeItem(CHATS_KEY); });

describe("chatNameForMeeting", () => {
  it("the bound chat's own name, by binding or by meeting chat id", () => {
    localStorage.setItem(CHATS_KEY, JSON.stringify([{ id: "pchat-a", label: "AIT planning", meeting: "119", workspaces: [] }]));
    expect(chatNameForMeeting("119")).toBe("AIT planning");
    localStorage.setItem(CHATS_KEY, JSON.stringify([{ id: "meet-119", label: "Sync with Ravi", workspaces: [] }]));
    expect(chatNameForMeeting("119")).toBe("Sync with Ravi");
  });
  it("nothing when no chat is bound, the chat has no name of its own, or storage is unreadable", () => {
    expect(chatNameForMeeting("119")).toBe("");
    localStorage.setItem(CHATS_KEY, JSON.stringify([{ id: "pchat-a", label: "New chat", meeting: "119", workspaces: [] }]));
    expect(chatNameForMeeting("119")).toBe("");
    localStorage.setItem(CHATS_KEY, "{not json");
    expect(chatNameForMeeting("119")).toBe("");
  });
  it("the sidebar names an untitled meeting by its chat — never its id", () => {
    expect(meetingName(row(), "", [], "AIT planning")).toBe("AIT planning");
    expect(meetingName(row(), "", ["Valeriia"], "")).toBe("Call with Valeriia");
    expect(meetingName(row({ title_custom: "Kickoff" }), "", [], "AIT planning")).toBe("Kickoff");
  });
});
