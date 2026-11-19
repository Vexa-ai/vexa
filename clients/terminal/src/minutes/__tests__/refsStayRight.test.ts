/** FOUNDER RULE 2026-10-16: a click inside a chat never replaces the chat in the centre; only the
 *  side panel changes. A call/meeting reference opens the meeting (transcript and notes) on the right. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import { meetingRefPage } from "../roomView";
import { openRef } from "../../ui-kit/RefChip";

describe("a meeting reference opens on the right", () => {
  it("a known meeting is its meeting page; an unknown one its notes page", () => {
    const ms = [{ id: 142, native_id: "abc-defg-hij", title: "AIT Lisbon planning call" }] as never[];
    expect(meetingRefPage("google_meet/abc-defg-hij", ms)).toEqual({ kind: "meeting", path: "142", label: "AIT Lisbon planning call" });
    expect(meetingRefPage("142", ms)).toEqual({ kind: "meeting", path: "142", label: "AIT Lisbon planning call" });
    expect(meetingRefPage("zzz", ms).kind).toBeUndefined();
  });
});

describe("a ref chip never moves the centre", () => {
  const seen: string[] = [];
  const on = (n: string) => () => seen.push(n);
  afterEach(() => { seen.length = 0; });
  it("a meeting chip opens the meeting on the right, never navigates or switches chat", () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    window.addEventListener(OPEN_MEETING_EVENT, on("meeting"));
    window.addEventListener(OPEN_SESSION_EVENT, on("session"));
    openRef({ access: "readable", kind: "meeting", label: "x", open: { meeting: 142 } } as never);
    expect(seen).toEqual(["meeting"]);
    expect(assign).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
