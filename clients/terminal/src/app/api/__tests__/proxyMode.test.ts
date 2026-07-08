import { afterEach, describe, expect, it } from "vitest";
import { refusedInMeetingsMode, MEETINGS_DOMAIN } from "../proxyMode";

/** Meetings-only mode gates the catch-all proxy: agent paths are refused, meeting-domain paths pass.
 *  (NEXT_PUBLIC_* is inlined at build time in the browser bundle, but the server routes read
 *  process.env at request time — which is what these tests exercise.) */
describe("proxyMode — meetings-only server gate", () => {
  afterEach(() => { delete process.env.NEXT_PUBLIC_TERMINAL_MODE; });

  it("default mode refuses nothing", () => {
    for (const p of ["meetings", "sessions", "chat", "routines", "workspace/tree", "bots"]) {
      expect(refusedInMeetingsMode(p)).toBe(false);
    }
  });

  it("meetings mode passes the meeting domain and refuses everything else", () => {
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    for (const p of ["meetings", "meetings/google_meet/abc", "transcripts/google_meet/abc", "bots", "bots/google_meet/abc"]) {
      expect(refusedInMeetingsMode(p)).toBe(false);
    }
    for (const p of ["sessions", "chat", "routines", "workspace/tree", "events", "models", "meetingsX"]) {
      expect(refusedInMeetingsMode(p)).toBe(true);
    }
  });

  it("MEETINGS_DOMAIN matches whole path segments only (no prefix bleed)", () => {
    expect(MEETINGS_DOMAIN.test("meetings")).toBe(true);
    expect(MEETINGS_DOMAIN.test("meetingsomething")).toBe(false);
    expect(MEETINGS_DOMAIN.test("botsy")).toBe(false);
  });

  it("user self-serve configs route to the gateway ROOT (calendar/webhook live in identity)", () => {
    expect(MEETINGS_DOMAIN.test("user/calendar")).toBe(true);
    expect(MEETINGS_DOMAIN.test("user/webhook")).toBe(true);
    expect(MEETINGS_DOMAIN.test("userdata")).toBe(false);
    // …and they stay reachable in meetings-only mode (the ICS popover lives on the Meetings surface)
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    expect(refusedInMeetingsMode("user/calendar")).toBe(false);
  });
});
