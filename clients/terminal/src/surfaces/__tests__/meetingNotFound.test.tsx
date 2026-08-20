/** What an addressable meeting URL does with an id that isn't ours.
 *
 *  `/meetings/<id>` can be pasted, bookmarked and reloaded, so a dead reference is a NORMAL outcome —
 *  a deleted row, a typo, someone else's un-shared meeting. It must read as an answer, never as a
 *  crash and never as a "Connecting…" that never ends. The resolution is a pure function of (row,
 *  list-has-answered) precisely so a network blip can't make a live meeting look deleted.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MeetingNotFound, meetingResolution } from "../meeting";
import type { MeetingMock } from "../meetingModel";

const row = (id: string): MeetingMock => ({
  id, native_id: "abc-defg-hij", title: "Google Meet · abc-defg-hij", when: "now",
  status: "past", live_status: "completed", platform: "Google Meet", has_recording: false,
  docs: [], participants: [], mentioned: [], actions: [], transcript: [], insights: [],
} as MeetingMock);

afterEach(cleanup);

describe("meetingResolution — (row, list answered) → what the tab shows", () => {
  it("row present → resolved", () => expect(meetingResolution(row("482"), true)).toBe("resolved"));
  it("no row and the list has NOT answered yet → still resolving (never a premature not-found)", () => {
    expect(meetingResolution(undefined, false)).toBe("resolving");
  });
  it("no row once the list HAS answered → not-found", () => {
    expect(meetingResolution(undefined, true)).toBe("not-found");
  });
  it("a resolved row is resolved even before the list settles (an optimistic row still renders)", () => {
    expect(meetingResolution(row("482"), false)).toBe("resolved");
  });
});

describe("MeetingNotFound — the clean dead end", () => {
  it("names the id that failed to resolve", () => {
    render(<MeetingNotFound meetingId="9999" />);
    expect(screen.getByText("Meeting not found")).toBeTruthy();
    expect(screen.getByText("9999")).toBeTruthy();
  });

  it("offers a way out when one is wired", () => {
    render(<MeetingNotFound meetingId="9999" onOpenToday={() => {}} />);
    expect(screen.getByRole("button", { name: "Open today" })).toBeTruthy();
  });

  it("an id-less route still renders an answer, not an empty shell", () => {
    render(<MeetingNotFound meetingId="" />);
    expect(screen.getByText("Meeting not found")).toBeTruthy();
  });
});
