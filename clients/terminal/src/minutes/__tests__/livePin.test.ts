/** A LIVE MEETING IS ALWAYS PINNED (founder, UI lab 2026-10-14): its tab is first, marked live, in
 *  ANY chat while it runs, and cannot be closed; when it ends it is an ordinary tab again. */
import { describe, expect, it } from "vitest";
import { pinLive } from "../livePin";
import type { Page } from "../types";
import type { MeetingMock } from "../../surfaces/meetingModel";

const pages: Page[] = [
  { kind: "doc", path: "README.md", slug: "ait", label: "AI Tinkerers Lisbon", desk: true },
  { kind: "meeting", path: "9121", label: "AIT planning call", pinned: true },
  { kind: "doc", path: "agenda.md", slug: "ait", label: "agenda" },
];
const m = (status: string) => ({ id: "9121", title: "AIT planning call", status, live_status: status }) as unknown as MeetingMock;

describe("the live meeting's tab", () => {
  it("is first, live and permanent while the meeting runs — once, even when the chat already had it", () => {
    const out = pinLive(pages, [m("active")]);
    expect(out[0]).toMatchObject({ kind: "meeting", path: "9121", live: true, permanent: true });
    expect(out.filter((p) => p.kind === "meeting" && p.path === "9121")).toHaveLength(1);
    expect(out.slice(1).map((p) => p.path)).toEqual(["README.md", "agenda.md"]);
  });

  it("appears in a chat that never opened it", () => {
    expect(pinLive([pages[0]], [m("active")]).map((p) => p.path)).toEqual(["9121", "README.md"]);
  });

  it("unpins when the meeting ends", () => {
    expect(pinLive(pages, [m("completed")])).toBe(pages);
  });
});
