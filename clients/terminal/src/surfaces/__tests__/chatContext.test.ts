/** chatContext — the terminal-state context bundle's pure client half (slice 1).
 *
 *  focusTarget maps every center-tab kind to its wire focus; scheduleEligible mirrors the
 *  server's ambient gate; buildChatContext shapes the exact wire object; the include toggle
 *  persists per session (and absence means "surface-gated default", not false).
 */
import { beforeEach, describe, expect, it } from "vitest";
import {
  buildChatContext, focusTarget, readIncludeSchedule, scheduleEligible, writeIncludeSchedule,
} from "../chatContext";

const tab = (kind: string, params: Record<string, unknown> = {}) => ({ kind, params });

describe("focusTarget", () => {
  it("maps doc/file tabs to a file ref", () => {
    expect(focusTarget(tab("doc", { path: "kg/notes.md" }))).toEqual({ kind: "file", ref: "@file:kg/notes.md" });
    expect(focusTarget(tab("file", { path: "a.md" }))).toEqual({ kind: "file", ref: "@file:a.md" });
  });
  it("maps meeting AND meetingPrep tabs to a meeting focus", () => {
    expect(focusTarget(tab("meeting", { meetingId: "51" }))).toEqual({ kind: "meeting", native_id: "51" });
    expect(focusTarget(tab("meetingPrep", { meetingId: "52" }))).toEqual({ kind: "meeting", native_id: "52" });
  });
  it("maps the workspace manage panel and the today tab", () => {
    expect(focusTarget(tab("workspace", { slug: "acme-deal", shared: true })))
      .toEqual({ kind: "workspace", slug: "acme-deal", shared: true });
    expect(focusTarget(tab("workspace", { slug: "mine" }))).toEqual({ kind: "workspace", slug: "mine", shared: undefined });
    expect(focusTarget(tab("today"))).toEqual({ kind: "today" });
  });
  it("returns null for unfocusable tabs and missing params", () => {
    expect(focusTarget(null)).toBeNull();
    expect(focusTarget(tab("canvas"))).toBeNull();
    expect(focusTarget(tab("meeting", {}))).toBeNull();
    expect(focusTarget(tab("doc", {}))).toBeNull();
  });
});

describe("scheduleEligible (mirrors the server gate)", () => {
  it("is on for the meetings list and meeting-ish tabs", () => {
    expect(scheduleEligible("meetings", null)).toBe(true);
    expect(scheduleEligible("files", tab("today"))).toBe(true);
    expect(scheduleEligible("files", tab("meeting", { meetingId: "1" }))).toBe(true);
    expect(scheduleEligible("files", tab("meetingPrep", { meetingId: "1" }))).toBe(true);
  });
  it("is off for doc surfaces", () => {
    expect(scheduleEligible("files", tab("doc", { path: "a.md" }))).toBe(false);
    expect(scheduleEligible("sessions", null)).toBe(false);
  });
});

describe("buildChatContext", () => {
  it("shapes the wire object; include only when explicitly toggled", () => {
    const ctx = buildChatContext({
      activeList: "meetings", activeTab: tab("meetingPrep", { meetingId: "51" }),
      focus: { kind: "meeting", native_id: "51" }, includeSchedule: null,
    });
    expect(ctx.surface).toEqual({ list: "meetings", tab: { kind: "meetingPrep" } });
    expect(ctx.focus).toEqual({ kind: "meeting", native_id: "51" });
    expect(ctx.include).toBeUndefined();
    expect(typeof ctx.tz === "string" || ctx.tz === undefined).toBe(true);
  });
  it("carries the explicit toggle and a cleared (null) focus", () => {
    const ctx = buildChatContext({ activeList: "files", activeTab: null, focus: null, includeSchedule: false });
    expect(ctx.include).toEqual({ schedule: false });
    expect(ctx.focus).toBeNull();
  });
});

describe("include-toggle persistence", () => {
  beforeEach(() => window.localStorage.clear());
  it("round-trips per session and clears on null", () => {
    expect(readIncludeSchedule("s1")).toBeNull();
    writeIncludeSchedule("s1", false);
    expect(readIncludeSchedule("s1")).toBe(false);
    expect(readIncludeSchedule("s2")).toBeNull();     // per-session
    writeIncludeSchedule("s1", null);
    expect(readIncludeSchedule("s1")).toBeNull();
  });
});
