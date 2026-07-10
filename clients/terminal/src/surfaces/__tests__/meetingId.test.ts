import { describe, expect, it } from "vitest";
import { isValidMeetingId, parseMeetingInput } from "../meetingId";

describe("isValidMeetingId", () => {
  it("accepts a well-formed Google Meet code, rejects malformed", () => {
    expect(isValidMeetingId("google_meet", "abc-defg-hij")).toBe(true);
    expect(isValidMeetingId("google_meet", "ABC-DEFG-HIJ")).toBe(true); // case-insensitive
    expect(isValidMeetingId("google_meet", "abc-def-hij")).toBe(false);
    expect(isValidMeetingId("google_meet", "abcdefghij")).toBe(false);
    expect(isValidMeetingId("google_meet", "")).toBe(false);
  });

  it("accepts 9-11 digit Zoom ids only", () => {
    expect(isValidMeetingId("zoom", "123456789")).toBe(true);
    expect(isValidMeetingId("zoom", "12345678901")).toBe(true);
    expect(isValidMeetingId("zoom", "12345")).toBe(false);
    expect(isValidMeetingId("zoom", "abc")).toBe(false);
  });

  it("accepts any non-empty Teams id", () => {
    expect(isValidMeetingId("teams", "19:meeting_xyz@thread.v2")).toBe(true);
    expect(isValidMeetingId("teams", "")).toBe(false);
  });

  it("accepts a single URL-safe Jitsi room, rejects separators/whitespace", () => {
    expect(isValidMeetingId("jitsi", "VexaStandup")).toBe(true);
    expect(isValidMeetingId("jitsi", "Team%20Sync")).toBe(true); // encoded form IS the id
    expect(isValidMeetingId("jitsi", "a/b")).toBe(false);
    expect(isValidMeetingId("jitsi", "has space")).toBe(false);
    expect(isValidMeetingId("jitsi", "")).toBe(false);
  });
});

describe("parseMeetingInput", () => {
  it("parses a bare Google Meet code", () => {
    expect(parseMeetingInput("abc-defg-hij")).toEqual({ platform: "google_meet", native_meeting_id: "abc-defg-hij" });
  });

  it("parses a Google Meet URL", () => {
    expect(parseMeetingInput("https://meet.google.com/abc-defg-hij")).toEqual({
      platform: "google_meet",
      native_meeting_id: "abc-defg-hij",
    });
  });

  it("parses a Zoom URL and a bare zoom id", () => {
    expect(parseMeetingInput("https://us02web.zoom.us/j/12345678901")).toEqual({
      platform: "zoom",
      native_meeting_id: "12345678901",
    });
    expect(parseMeetingInput("1234567890")).toEqual({ platform: "zoom", native_meeting_id: "1234567890" });
  });

  it("parses a Teams meeting thread id from a URL", () => {
    const url =
      "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc123%40thread.v2/0";
    expect(parseMeetingInput(url)).toEqual({
      platform: "teams",
      native_meeting_id: "19:meeting_abc123@thread.v2",
    });
  });

  it("parses the new short Teams meeting link (teams.microsoft.com/meet/<id>?p=…)", () => {
    expect(parseMeetingInput("https://teams.microsoft.com/meet/33832851446746?p=Y16HhbYoEs9At3lGtb")).toEqual({
      platform: "teams",
      native_meeting_id: "33832851446746",
    });
  });

  it("parses a meet.jit.si room URL (case + encoding preserved)", () => {
    expect(parseMeetingInput("https://meet.jit.si/VexaStandup")).toEqual({
      platform: "jitsi",
      native_meeting_id: "VexaStandup",
    });
    expect(parseMeetingInput("https://meet.jit.si/Team%20Sync/")).toEqual({
      platform: "jitsi",
      native_meeting_id: "Team%20Sync",
    });
  });

  it("infers jitsi for self-hosted conventions (*jitsi* and meet.* hosts)", () => {
    expect(parseMeetingInput("https://jitsi.example.org/MyRoom")).toEqual({
      platform: "jitsi",
      native_meeting_id: "MyRoom",
    });
    expect(parseMeetingInput("https://meet.example.org/TeamSync")).toEqual({
      platform: "jitsi",
      native_meeting_id: "TeamSync",
    });
    // meet.google.com is claimed by the Meet rule above — never captured by the meet.* fallback.
    expect(parseMeetingInput("https://meet.google.com/abc-defg-hij")?.platform).toBe("google_meet");
  });

  it("does not infer jitsi for a bare origin or a multi-segment path", () => {
    expect(parseMeetingInput("https://meet.jit.si/")).toBeNull();
    expect(parseMeetingInput("https://jitsi.example.org/a/b")).toBeNull();
  });

  it("returns null for garbage", () => {
    expect(parseMeetingInput("")).toBeNull();
    expect(parseMeetingInput("not a meeting")).toBeNull();
    expect(parseMeetingInput("https://example.com/whatever")).toBeNull();
  });
});
