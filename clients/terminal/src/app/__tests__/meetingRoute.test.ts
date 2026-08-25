/** The addressable-meeting URL contract: `/meetings/<id>` parses back to exactly the id that was put
 *  in, refuses anything it can't own, and never widens into another route. */
import { describe, it, expect } from "vitest";
import { isMeetingRouteId, isOwnedPath, meetingIdFromPath, meetingPath } from "../meetingRoute";

describe("meetingPath — id → URL", () => {
  it("a row id becomes /meetings/<id>", () => expect(meetingPath("482")).toBe("/meetings/482"));
  it("a native meeting code survives", () => expect(meetingPath("abc-defg-hij")).toBe("/meetings/abc-defg-hij"));
  it("a teams thread id is percent-encoded", () => {
    expect(meetingPath("19:meeting_abc@thread.v2")).toBe("/meetings/19%3Ameeting_abc%40thread.v2");
  });
  it("an unusable id falls back to the home route (never a broken URL)", () => {
    expect(meetingPath("")).toBe("/");
    expect(meetingPath("a/b")).toBe("/");
    expect(meetingPath("has space")).toBe("/");
  });
});

describe("meetingIdFromPath — URL → id", () => {
  it("reads the id back", () => expect(meetingIdFromPath("/meetings/482")).toBe("482"));
  it("decodes percent-encoding (round-trips meetingPath)", () => {
    const id = "19:meeting_abc@thread.v2";
    expect(meetingIdFromPath(meetingPath(id))).toBe(id);
  });
  it("tolerates a trailing slash", () => expect(meetingIdFromPath("/meetings/482/")).toBe("482"));
  it("is null off the meeting route", () => {
    expect(meetingIdFromPath("/")).toBeNull();
    expect(meetingIdFromPath("/meetings")).toBeNull();
    expect(meetingIdFromPath("/settings/482")).toBeNull();
    expect(meetingIdFromPath(null)).toBeNull();
  });
  it("rejects an empty id, extra segments, and a malformed escape", () => {
    expect(meetingIdFromPath("/meetings/")).toBeNull();
    expect(meetingIdFromPath("/meetings/482/transcript")).toBeNull();
    expect(meetingIdFromPath("/meetings/%E0%A4%A")).toBeNull();
  });
  it("rejects an id that could escape the segment", () => {
    expect(isMeetingRouteId("../../api/admin")).toBe(false);
    expect(isMeetingRouteId("a".repeat(129))).toBe(false);
    expect(isMeetingRouteId("482")).toBe(true);
  });
});

describe("isOwnedPath — which URLs the sync effect may rewrite", () => {
  it("owns home and meeting routes", () => {
    expect(isOwnedPath("/")).toBe(true);
    expect(isOwnedPath("/meetings/482")).toBe(true);
  });
  it("leaves every other route alone", () => {
    expect(isOwnedPath("/debug/transcript")).toBe(false);
    expect(isOwnedPath("/meetings/482/extra")).toBe(false);
  });
});
