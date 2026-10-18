import { describe, expect, it } from "vitest";
import { botStateHeadline, isTranscriptStale, meetingHealth, STALE_MS } from "../meetingHealth";
import { shouldForceReconnect } from "../../surfaces/meetingLive";

const now = 1_000_000;

describe("isTranscriptStale", () => {
  it("is not stale when a line just landed", () => {
    expect(isTranscriptStale(now - 1000, now)).toBe(false);
  });
  it("is stale once past the threshold", () => {
    expect(isTranscriptStale(now - STALE_MS - 1, now)).toBe(true);
    expect(isTranscriptStale(now - STALE_MS, now)).toBe(true);
  });
  it("is never stale before the first line (connecting, not stalled)", () => {
    expect(isTranscriptStale(undefined, now)).toBe(false);
  });
});

describe("meetingHealth verdict", () => {
  it("ok: connected, fresh, no issues", () => {
    expect(meetingHealth({ liveConnected: true, lastTranscriptAt: now - 1000 }, now, true).kind).toBe("ok");
  });
  it("ended wins over staleness (clean end, not stalled)", () => {
    expect(meetingHealth({ ended: true, lastTranscriptAt: now - STALE_MS - 5000 }, now, true).kind).toBe("ended");
  });
  it("disconnected when connected is false", () => {
    const h = meetingHealth({ liveConnected: false, reconnects: 3, lastTranscriptAt: now - 1000 }, now, true);
    expect(h.kind).toBe("disconnected");
    expect(h.reconnects).toBe(3);
  });
  it("stalled when connected but no new line past threshold", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: now - STALE_MS - 1 }, now, true);
    expect(h.kind).toBe("stalled");
    expect(h.staleForMs).toBeGreaterThanOrEqual(STALE_MS);
  });
  it("surfaces a feed (parse) error when otherwise healthy", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: now - 500, issues: [{ kind: "parse", message: "boom", at: now }] }, now, true);
    expect(h.kind).toBe("error");
    expect(h.latestIssue?.kind).toBe("parse");
  });
  it("recorded (not live) meeting is never disconnected/stalled", () => {
    expect(meetingHealth({ liveConnected: false, lastTranscriptAt: now - STALE_MS - 1 }, now, false).kind).toBe("ok");
  });

  it("at the door while the bot is on its way in and nothing has been heard", () => {
    for (const status of ["requested", "joining", "awaiting_admission"]) {
      const h = meetingHealth({ liveConnected: true }, now, true, STALE_MS, status);
      expect(h.kind).toBe("at-door");
      expect(h.needsHelp).toBe(false);
    }
    expect(meetingHealth({ liveConnected: true }, now, true, STALE_MS, "needs_help").needsHelp).toBe(true);
  });

  it("leaves the door the moment a first line lands, even on a lagging status", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: now - 1000 }, now, true, STALE_MS, "joining");
    expect(h.kind).toBe("ok");
  });

  it("a dropped feed outranks the door (the loudest live failure wins)", () => {
    expect(meetingHealth({ liveConnected: false }, now, true, STALE_MS, "joining").kind).toBe("disconnected");
  });

  it("never guesses the door from silence alone — an active meeting with no line yet is ok", () => {
    expect(meetingHealth({ liveConnected: true }, now, true, STALE_MS, "active").kind).toBe("ok");
  });
});

describe("botStateHeadline — the words name the BOT, never the product's work", () => {
  const h = (kind: string, extra: Record<string, unknown> = {}) =>
    botStateHeadline({ kind, reconnects: 0, ...extra } as never);

  it("says where the bot is", () => {
    expect(h("at-door")).toBe("Bot at the door");
    expect(h("at-door", { needsHelp: true })).toBe("Bot needs someone to let it in");
    expect(h("ended")).toBe("Bot left");
    expect(h("disconnected")).toBe("Reconnecting to the bot\u2026");
    expect(h("stalled", { staleForMs: 24_000 })).toBe("Bot admitted \u00b7 no words for 24s");
    expect(h("stalled")).toBe("Bot admitted \u00b7 no words for a while");
  });

  it("carries no wording that could read as the product processing anything (decision 34)", () => {
    const banned = /waiting for transcript|processing|cleaned|copilot|model|inference|analy/i;
    for (const kind of ["at-door", "ended", "disconnected", "stalled", "error"]) {
      expect(h(kind, { staleForMs: 24_000, needsHelp: false })).not.toMatch(banned);
    }
  });
});

describe("shouldForceReconnect (watchdog predicate)", () => {
  it("reconnects once no event for longer than the threshold", () => {
    expect(shouldForceReconnect(now - 20001, now)).toBe(true);
  });
  it("does not reconnect within the threshold (pings keep it alive)", () => {
    expect(shouldForceReconnect(now - 14000, now)).toBe(false);
    expect(shouldForceReconnect(now - 20000, now)).toBe(false);
  });
  it("does not reconnect before any event has arrived", () => {
    expect(shouldForceReconnect(undefined, now)).toBe(false);
  });
});
