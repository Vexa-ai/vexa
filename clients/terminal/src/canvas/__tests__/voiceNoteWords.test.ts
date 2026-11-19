/** A VOICE NOTE never speaks the bot's language (#1783; founder, 2026-10-13: "Bot at the door" and a
 *  header reading "voice_note · requested" make no sense for a note recorded into one's own phone).
 *  Every word the state card and the meeting header can say for a voice_note meeting is checked. */
import { describe, expect, it } from "vitest";
import { botStateHeadline, meetingHealth, type MeetingHealth } from "../meetingHealth";
import { meetingHeader, meetingStatus } from "../../minutes/MeetingPageHeader";
import type { MeetingMock } from "../../surfaces/meetingModel";

const BOT_WORDS = /\bbot\b|door|lobby|admi|join|let it in|voice_note|requested|awaiting/i;
const KINDS: MeetingHealth[] = [
  { kind: "at-door", reconnects: 0 }, { kind: "at-door", reconnects: 0, needsHelp: true },
  { kind: "ended", reconnects: 0 }, { kind: "disconnected", reconnects: 1 },
  { kind: "stalled", reconnects: 0, staleForMs: 30_000 }, { kind: "error", reconnects: 0 },
];
const STATUSES = ["requested", "joining", "awaiting_admission", "needs_help", "active", "stopping", "failed", "completed"];
const note = (live_status: string): MeetingMock => ({
  id: "41", native_id: "vn-0c600b9fdb1f", platform: "voice_note", title: "Voice note", status: "live", live_status,
  start_time: "2026-10-13T08:04:50Z", participants: [],
} as unknown as MeetingMock);

describe("voice note words", () => {
  it("the state card says the note's state, never the bot's", () => {
    for (const h of KINDS) expect(botStateHeadline(h, "voice_note")).not.toMatch(BOT_WORDS);
    expect(botStateHeadline({ kind: "at-door", reconnects: 0 }, "voice_note")).toBe("Starting…");
    expect(botStateHeadline({ kind: "ended", reconnects: 0 }, "voice_note")).toBe("Ended");
    // a meeting keeps the bot's words
    expect(botStateHeadline({ kind: "at-door", reconnects: 0 })).toBe("Bot at the door");
  });
  it("every status a note passes through reads as a note in the header", () => {
    const now = Date.parse("2026-10-13T08:06:02Z");
    for (const s of STATUSES) {
      const { metadata } = meetingHeader("", note(s), now);
      expect(metadata).not.toMatch(BOT_WORDS);
      expect(metadata.startsWith("Voice note · ")).toBe(true);
    }
    expect(meetingStatus(note("active"), now)).toBe("Recording · 01:12");
    expect(meetingStatus(note("requested"), now)).toBe("Starting…");
    expect(meetingStatus(note("stopping"), now)).toBe("Saving…");
  });
  it("the health verdict for a note waiting to start is the starting state", () => {
    const h = meetingHealth({}, Date.now(), true, 20_000, "requested");
    expect(botStateHeadline(h, "voice_note")).toBe("Starting…");
  });
});
