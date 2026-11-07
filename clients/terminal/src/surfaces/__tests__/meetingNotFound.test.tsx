/** What an addressable meeting URL does with an id that isn't ours.
 *
 *  `/meetings/<id>` can be pasted, bookmarked and reloaded, so a dead reference is a NORMAL outcome —
 *  a deleted row, a typo, someone else's un-shared meeting. It must read as an answer, never as a
 *  crash and never as a "Connecting…" that never ends. The resolution is a pure function of (row,
 *  list-has-answered) precisely so a network blip can't make a live meeting look deleted.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import {
  MEETING_LOOKUP_GRACE_MS, MeetingLookingUp, MeetingNotFound, meetingLookupState, meetingResolution,
} from "../meeting";
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

/** The 2026-09-05 defect, at the boundary that decided it.
 *
 *  The chat sent a bot into `google_meet/edh-vofu-jxm`; meeting-api made row 132; the gateway served
 *  `GET /meetings/132` 200 and listed it — and the Transcript tab read "Meeting not found — Nothing
 *  here matches 132". `meetingResolution` was right about its inputs and wrong about the world: it
 *  treats `listLoaded` as "the list is COMPLETE", when the flag can only honestly mean "the list
 *  ANSWERED once", and a row created mid-session is not in that answer.
 */
describe("meetingLookupState — an absent id is a question before it is a verdict", () => {
  it("no row, list answered, grace unspent → SEARCHING (never an immediate verdict)", () => {
    expect(meetingLookupState(undefined, true, false)).toBe("searching");
  });

  it("…and not-found only once the grace IS spent — i.e. after we re-asked and still nothing", () => {
    expect(meetingLookupState(undefined, true, true)).toBe("not-found");
  });

  it("a row that lands DURING the grace resolves — which is the whole point of waiting", () => {
    expect(meetingLookupState(row("132"), true, false)).toBe("resolved");
    expect(meetingLookupState(row("132"), true, true)).toBe("resolved");
  });

  it("P0 UNTOUCHED: an unanswered list keeps resolving — a blip still cannot look like a deletion", () => {
    expect(meetingLookupState(undefined, false, false)).toBe("resolving");
    expect(meetingLookupState(undefined, false, true)).toBe("resolving");
  });

  it("agrees with meetingResolution everywhere except the one state it adds", () => {
    for (const [m, loaded] of [[row("482"), true], [row("482"), false], [undefined, false]] as const)
      expect(meetingLookupState(m, loaded, false)).toBe(meetingResolution(m, loaded));
  });

  it("the grace is ONE snapshot round-trip, not a poll and not a hang", () => {
    expect(MEETING_LOOKUP_GRACE_MS).toBeGreaterThan(0);
    expect(MEETING_LOOKUP_GRACE_MS).toBeLessThanOrEqual(8_000);
  });
});

describe("MeetingLookingUp — the neutral face of `searching`", () => {
  it("says it is still asking, and does NOT say the meeting is missing", () => {
    render(<MeetingLookingUp meetingId="132" />);
    expect(screen.getByText("Looking for this meeting…")).toBeTruthy();
    expect(screen.getByText("132")).toBeTruthy();
    expect(screen.queryByText("Meeting not found")).toBeNull();
  });

  it("an id-less route still renders an answer, not an empty shell", () => {
    render(<MeetingLookingUp meetingId="" />);
    expect(screen.getByText("Looking for this meeting…")).toBeTruthy();
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
