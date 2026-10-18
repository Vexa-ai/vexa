import { describe, expect, it } from "vitest";
import { botStateHeadline, isTranscriptStale, meetingHealth, STALE_MS, wordsSeen } from "../meetingHealth";
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

describe("transcription progress (catching up vs a quiet room)", () => {
  const stale = now - STALE_MS - 5000;
  it("behind: speech is queued for transcription — catching up, not 'no words'", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: stale, progress: { backlogS: 42, silenceS: 1, finishing: false, at: now - 1000 } }, now, true);
    expect(h.kind).toBe("behind");
    expect(botStateHeadline(h)).toBe("Transcription delayed — catching up (42s behind)");
  });
  it("a quiet room stays 'no words' even with audio still queued (the queue is all silence)", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: stale, progress: { backlogS: 12, silenceS: 30, finishing: false, at: now - 1000 } }, now, true);
    expect(h.kind).toBe("stalled");
  });
  it("a small backlog is normal, not news", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: now - 1000, progress: { backlogS: 6, silenceS: 0, finishing: false, at: now - 1000 } }, now, true);
    expect(h.kind).toBe("ok");
  });
  it("finishing: after stop, until the final pass ends", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: stale, progress: { backlogS: 20, silenceS: 5, finishing: true, at: now - 1000 } }, now, true);
    expect(h.kind).toBe("finishing");
    expect(botStateHeadline(h)).toBe("Finishing transcript…");
  });
  it("an old progress report is ignored", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: stale, progress: { backlogS: 40, silenceS: 0, finishing: false, at: now - 60_000 } }, now, true);
    expect(h.kind).toBe("stalled");
  });
});


// app.dev 2026-10-16 ("Call with dmitry"): "Bot admitted · no words for 50s" stayed up while the call's
// transcript was plainly flowing (20 lines, live). The verdict read only the live stream's last line
// time; the lines on screen are evidence too. A segment the reader can SEE arrive clears it.
describe("words on screen clear the no-words verdict", () => {
  it("notes the time a new segment appears, and keeps it while nothing changes", () => {
    const a = wordsSeen({ sig: "" }, [{ id: "1", text: "hello" }], now);
    expect(a.at).toBe(now);
    expect(wordsSeen(a, [{ id: "1", text: "hello" }], now + 5000).at).toBe(now);
    expect(wordsSeen(a, [{ id: "1", text: "hello" }, { id: "2", text: "next" }], now + 5000).at).toBe(now + 5000);
    expect(wordsSeen(a, [{ id: "1", text: "hello there" }], now + 7000).at).toBe(now + 7000);   // a draft refining
    expect(wordsSeen({ sig: "" }, [], now).at).toBeUndefined();
    expect(wordsSeen({ sig: null }, [{ id: "1", text: "already there" }], now).at).toBeUndefined();   // a baseline, not news
  });
  it("is not stalled when segments keep landing although the live stream went quiet", () => {
    const streamLast = now - 50_000;
    const seen = wordsSeen({ sig: "" }, [{ id: "9", text: "still talking" }], now - 1000);
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: Math.max(streamLast, seen.at ?? 0) }, now, true, STALE_MS, "active");
    expect(h.kind).toBe("ok");
  });
  it("clears 'at the door' on the first segment", () => {
    const seen = wordsSeen({ sig: "" }, [{ id: "1", text: "hi" }], now);
    expect(meetingHealth({ lastTranscriptAt: seen.at }, now, true, STALE_MS, "awaiting_admission").kind).toBe("ok");
  });
});


describe("a stream that dropped and came back (founder 2026-10-16)", () => {
  const issue = { kind: "stream" as const, message: "Meeting stream disconnected; reconnecting", at: 1000 };
  it("is a quiet recovered line once words flow again, then nothing", () => {
    const d = { issues: [issue], liveConnected: true, lastTranscriptAt: 5000 };
    expect(meetingHealth(d as never, 6000, true).kind).toBe("recovered");
    expect(meetingHealth(d as never, 1000 + 61_000, true).kind).not.toBe("error");
  });
  it("a stream issue with no words since stays loud", () => {
    const d = { issues: [issue], liveConnected: true, lastTranscriptAt: 900 };
    expect(meetingHealth(d as never, 1500, true).kind).toBe("error");
  });
});
