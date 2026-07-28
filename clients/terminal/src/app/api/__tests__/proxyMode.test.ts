import { afterEach, describe, expect, it } from "vitest";
import * as proxyMode from "../proxyMode";

const refusedInMeetingsMode = proxyMode.refusedInMeetingsMode as (
  path: string,
  alloyEnabled: boolean,
) => boolean;
const isMeetingsDomain = (proxyMode as typeof proxyMode & {
  isMeetingsDomain?: (path: string, alloyEnabled: boolean) => boolean;
}).isMeetingsDomain;

/** Meetings-only mode gates the catch-all proxy: agent paths are refused, meeting-domain paths pass.
 *  (NEXT_PUBLIC_* is inlined at build time in the browser bundle, but the server routes read
 *  process.env at request time — which is what these tests exercise.) */
describe("proxyMode — meetings-only server gate", () => {
  afterEach(() => {
    delete process.env.NEXT_PUBLIC_TERMINAL_MODE;
    delete process.env.ALLOY_STT_TELEMETRY;
  });

  it("default mode refuses nothing", () => {
    for (const p of ["meetings", "sessions", "chat", "routines", "workspace/tree", "bots"]) {
      expect(refusedInMeetingsMode(p, false)).toBe(false);
    }
  });

  it("meetings mode passes the meeting domain and refuses everything else", () => {
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    for (const p of ["meetings", "meetings/google_meet/abc", "transcripts/google_meet/abc", "bots", "bots/google_meet/abc"]) {
      expect(refusedInMeetingsMode(p, false)).toBe(false);
    }
    for (const p of ["sessions", "chat", "routines", "workspace/tree", "events", "models", "meetingsX"]) {
      expect(refusedInMeetingsMode(p, false)).toBe(true);
    }
  });

  it("MEETINGS_DOMAIN matches whole path segments only (no prefix bleed)", () => {
    expect(proxyMode.MEETINGS_DOMAIN.test("meetings")).toBe(true);
    expect(proxyMode.MEETINGS_DOMAIN.test("meetingsomething")).toBe(false);
    expect(proxyMode.MEETINGS_DOMAIN.test("botsy")).toBe(false);
  });

  it("user self-serve configs route to the gateway ROOT (calendar/webhook live in identity)", () => {
    expect(proxyMode.MEETINGS_DOMAIN.test("user/calendar")).toBe(true);
    expect(proxyMode.MEETINGS_DOMAIN.test("user/webhook")).toBe(true);
    expect(proxyMode.MEETINGS_DOMAIN.test("userdata")).toBe(false);
    // …and they stay reachable in meetings-only mode (the ICS popover lives on the Meetings surface)
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    expect(refusedInMeetingsMode("user/calendar", false)).toBe(false);
  });

  it("keeps ALLOY outside the unconditional meeting-domain regex", () => {
    expect(proxyMode.MEETINGS_DOMAIN.test("alloy/stt/status")).toBe(false);
    expect(proxyMode.MEETINGS_DOMAIN.test("alloyed/stt/status")).toBe(false);
  });

  it("makes ALLOY routing an explicit pure flag-dependent decision", () => {
    expect(isMeetingsDomain).toBeTypeOf("function");
    expect(isMeetingsDomain!("alloy/stt/status", false)).toBe(false);
    expect(isMeetingsDomain!("alloy/stt/status", true)).toBe(true);
    expect(isMeetingsDomain!("alloyed/stt/status", true)).toBe(false);
  });

  it("refuses disabled ALLOY in meetings-only mode but permits it when enabled", () => {
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    expect(refusedInMeetingsMode("alloy/stt/status", false)).toBe(true);
    expect(refusedInMeetingsMode("alloy/stt/status", true)).toBe(false);
  });
});
