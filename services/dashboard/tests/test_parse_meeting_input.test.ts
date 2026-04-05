/**
 * Unit tests for parseMeetingInput — the URL/code parser that determines
 * which platform a meeting belongs to.
 */
import { describe, it, expect } from "vitest";
import { parseMeetingInput } from "@/lib/parse-meeting-input";

describe("parseMeetingInput", () => {
  describe("empty / invalid input", () => {
    it("returns null for empty string", () => {
      expect(parseMeetingInput("")).toBeNull();
    });

    it("returns null for whitespace only", () => {
      expect(parseMeetingInput("   ")).toBeNull();
    });
  });

  describe("Google Meet", () => {
    it("parses full URL", () => {
      const r = parseMeetingInput("https://meet.google.com/abc-defg-hij");
      expect(r).toEqual({ platform: "google_meet", meetingId: "abc-defg-hij" });
    });

    it("parses URL without protocol", () => {
      const r = parseMeetingInput("meet.google.com/abc-defg-hij");
      expect(r).toEqual({ platform: "google_meet", meetingId: "abc-defg-hij" });
    });

    it("parses bare meeting code", () => {
      const r = parseMeetingInput("abc-defg-hij");
      expect(r).toEqual({ platform: "google_meet", meetingId: "abc-defg-hij" });
    });

    it("lowercases the meeting code", () => {
      const r = parseMeetingInput("ABC-DEFG-HIJ");
      expect(r?.meetingId).toBe("abc-defg-hij");
    });
  });

  describe("Zoom", () => {
    it("parses standard zoom URL", () => {
      const r = parseMeetingInput("https://zoom.us/j/85173157171?pwd=secret");
      expect(r?.platform).toBe("zoom");
      expect(r?.meetingId).toBe("85173157171");
      expect(r?.passcode).toBe("secret");
    });

    it("parses subdomain zoom URL", () => {
      const r = parseMeetingInput("https://us05web.zoom.us/j/85173157171");
      expect(r?.platform).toBe("zoom");
      expect(r?.meetingId).toBe("85173157171");
    });

    it("parses bare zoom meeting ID (9-11 digits)", () => {
      const r = parseMeetingInput("851731571");
      expect(r?.platform).toBe("zoom");
      expect(r?.meetingId).toBe("851731571");
    });
  });

  describe("Microsoft Teams", () => {
    it("parses teams.live.com URL with passcode", () => {
      const r = parseMeetingInput(
        "https://teams.live.com/meet/9387167464734?p=qxJanYOcdjN4d6UlGa"
      );
      expect(r?.platform).toBe("teams");
      expect(r?.passcode).toBe("qxJanYOcdjN4d6UlGa");
      expect(r?.originalUrl).toBeDefined();
    });

    it("parses teams.microsoft.com meetup-join URL", () => {
      const r = parseMeetingInput(
        "https://teams.microsoft.com/l/meetup-join/meeting123"
      );
      expect(r?.platform).toBe("teams");
    });

    it("parses bare Teams meeting ID (12+ digits)", () => {
      const r = parseMeetingInput("938716746473400");
      expect(r?.platform).toBe("teams");
    });
  });
});
