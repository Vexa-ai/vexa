import { describe, expect, it } from "vitest";
import { isTranscriptStale, meetingHealth, STALE_MS } from "../meetingHealth";
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
  it("surfaces a model/parse error when otherwise healthy", () => {
    const h = meetingHealth({ liveConnected: true, lastTranscriptAt: now - 500, issues: [{ kind: "model", message: "boom", at: now }] }, now, true);
    expect(h.kind).toBe("error");
    expect(h.latestIssue?.kind).toBe("model");
  });
  it("recorded (not live) meeting is never disconnected/stalled", () => {
    expect(meetingHealth({ liveConnected: false, lastTranscriptAt: now - STALE_MS - 1 }, now, false).kind).toBe("ok");
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
