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

  it("infers jitsi for self-hosted conventions (*jitsi* hosts, and a 'meet' host label)", () => {
    // Non-canonical deployments get a deployment-scoped id (room@host) — same-named rooms
    // on different hosts never share an identity key. Mirrors the server parsers.
    expect(parseMeetingInput("https://jitsi.example.org/MyRoom")).toEqual({
      platform: "jitsi",
      native_meeting_id: "MyRoom@jitsi.example.org",
    });
    expect(parseMeetingInput("https://meet.example.org/TeamSync")).toEqual({
      platform: "jitsi",
      native_meeting_id: "TeamSync@meet.example.org",
    });
    // Regionalized deployments put "meet" mid-hostname (eu.meet.example.org).
    expect(parseMeetingInput("https://eu.meet.example.org/QualifiedRoomName")).toEqual({
      platform: "jitsi",
      native_meeting_id: "QualifiedRoomName@eu.meet.example.org",
    });
    // "meet" must be a whole label — meetings.example.org is NOT a jitsi convention.
    expect(parseMeetingInput("https://meetings.example.org/Room")).toBeNull();
    // meet.google.com is claimed by the Meet rule above — never captured by the fallback.
    expect(parseMeetingInput("https://meet.google.com/abc-defg-hij")?.platform).toBe("google_meet");
  });

  it("recognizes VEXA_JITSI_HOSTS-declared hosts via the jitsiHosts parameter", () => {
    // Without the declared list, a host with no jitsi/meet naming is rejected…
    expect(parseMeetingInput("https://calls.example.io/Standup")).toBeNull();
    // …with it, the link parses exactly like it does server-side.
    expect(parseMeetingInput("https://calls.example.io/Standup", ["calls.example.io"])).toEqual({
      platform: "jitsi",
      native_meeting_id: "Standup@calls.example.io",
    });
  });

  it("does not infer jitsi for a bare origin or a multi-segment path", () => {
    expect(parseMeetingInput("https://meet.jit.si/")).toBeNull();
    expect(parseMeetingInput("https://jitsi.example.org/a/b")).toBeNull();
  });

  it("matches platform hosts exactly or as a dotted subdomain, never by substring", () => {
    // A substring test puts the host under the control of whoever pasted the link: the platform
    // name becomes a prefix of a domain they registered. Mirrors the server parsers' rule.
    for (const url of [
      // A "meet" LABEL would otherwise re-admit these through the self-hosted jitsi door.
      "https://meet.google.com.attacker.example/abc-defg-hij",
      "https://meet.zoom.us.attacker.example/j/12345678901",
      "https://meet.teams.microsoft.com.evil.example/meet/33832851446746",
      // Substring / suffix-without-the-dot matches on the zoom and teams branches.
      "https://zoom.us.attacker.example/j/12345678901",
      "https://zoomgov.com.attacker.example/j/12345678901",
      "https://zoom.attacker.example/j/12345678901",
      "https://notzoom.com/j/12345678901",
      "https://notteams.live.com/meet/33832851446746",
      "https://eviltteams.live.com/meet/33832851446746",
      "https://teams.live.com.evil.example/meet/33832851446746",
      "https://teams.microsoft.com.evil.example/l/meetup-join/19%3ameeting_abc123%40thread.v2/0",
    ]) {
      expect(parseMeetingInput(url), url).toBeNull();
    }
  });

  it("keeps every real platform host parsing", () => {
    const cases: [string, string, string][] = [
      ["https://meet.google.com/abc-defg-hij", "google_meet", "abc-defg-hij"],
      ["https://zoom.us/j/12345678901", "zoom", "12345678901"],
      ["https://us02web.zoom.us/j/12345678901", "zoom", "12345678901"],
      ["https://company.zoom.us/j/12345678901?pwd=xyz", "zoom", "12345678901"],
      ["https://zoomgov.com/j/12345678901", "zoom", "12345678901"],
      ["https://frbmeetings.zoomgov.com/j/12345678901", "zoom", "12345678901"],
      ["https://teams.live.com/meet/33832851446746?p=abc", "teams", "33832851446746"],
      ["https://teams.microsoft.com/meet/33832851446746?p=abc", "teams", "33832851446746"],
      ["https://foo.teams.microsoft.com/meet/33832851446746?p=abc", "teams", "33832851446746"],
      ["https://meet.jit.si/VexaStandup", "jitsi", "VexaStandup"],
      ["https://jitsi.example.org/MyRoom", "jitsi", "MyRoom@jitsi.example.org"],
      ["https://meet.example.org/TeamSync", "jitsi", "TeamSync@meet.example.org"],
      ["https://eu.meet.example.org/Weekly", "jitsi", "Weekly@eu.meet.example.org"],
    ];
    for (const [url, platform, native_meeting_id] of cases) {
      expect(parseMeetingInput(url), url).toEqual({ platform, native_meeting_id });
    }
  });

  it("honours a declared jitsi host even when it looks like a platform", () => {
    // An operator naming their own deployment is an explicit opt-in, not a guess, so the
    // lookalike rule applies only to the naming heuristics.
    expect(parseMeetingInput("https://meet.google.com.internal.example/Standup", ["meet.google.com.internal.example"])).toEqual({
      platform: "jitsi",
      native_meeting_id: "Standup@meet.google.com.internal.example",
    });
  });

  it("returns null for garbage", () => {
    expect(parseMeetingInput("")).toBeNull();
    expect(parseMeetingInput("not a meeting")).toBeNull();
    expect(parseMeetingInput("https://example.com/whatever")).toBeNull();
  });
});
