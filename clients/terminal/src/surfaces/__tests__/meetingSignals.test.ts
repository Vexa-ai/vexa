/** Transcript signals reach the terminal on the shared `/ws` socket (ws.v1 `meeting.signals`).
 *
 *  Pinned to the sealed golden (vendored from core/gateway/contracts/ws.v1/golden), and the socket
 *  side: a meeting view that wants signals sends ONE `subscribe` naming `streams:["signals"]` (never
 *  the transcript a second time — the view already reads it over its own feed), re-sends it after a
 *  reconnect, and unsubscribes when the last viewer leaves. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import golden from "./fixtures/MeetingSignals.batch.json";
import { apiPlatform, mergeAnnotations, parseSignalsFrame } from "../meetingSignals";
import { subscribeMeetingStreams, __resetGatewayWS } from "../gatewayWS";

describe("parseSignalsFrame pinned to the ws.v1 golden", () => {
  it("reads the sealed frame", () => {
    const f = parseSignalsFrame(golden)!;
    expect(f).not.toBeNull();
    expect(f.meeting).toEqual({ id: "42", platform: "google_meet", native_id: "abc-defg-hij" });
    expect(f.annotations[0].segment_id).toBe("seg-17");
    expect(f.annotations[0].scores.decision).toBe(0.91);
    expect(f.annotations[0].words.Friday).toBe(0.82);
  });
  it("rejects other frames and drops malformed annotations", () => {
    expect(parseSignalsFrame({ type: "meeting.status", status: "active" })).toBeNull();
    expect(parseSignalsFrame(null)).toBeNull();
    const f = parseSignalsFrame({ ...golden, annotations: [{ id: "1-0" }, golden.annotations[0], "x"] })!;
    expect(f.annotations.map((a) => a.segment_id)).toEqual(["seg-17"]);
  });
});

describe("mergeAnnotations", () => {
  it("keys by segment and keeps the newest entry for a segment", () => {
    const a = { id: "1-0", segment_id: "s1", scores: { decision: 0.2 }, words: {} };
    const b = { id: "2-0", segment_id: "s1", scores: { decision: 0.9 }, words: {} };
    const merged = mergeAnnotations(mergeAnnotations({}, [b]), [a]);
    expect(merged.s1.scores.decision).toBe(0.9);
  });
  it("answers the api platform for a display name", () => {
    expect(apiPlatform("Google Meet")).toBe("google_meet");
    expect(apiPlatform("teams")).toBe("teams");
  });
});

class FakeSocket {
  static all: FakeSocket[] = [];
  sent: unknown[] = [];
  readyState = 0;
  onopen?: () => void; onmessage?: (m: { data: string }) => void; onclose?: (e: { code: number }) => void; onerror?: () => void;
  constructor(public url: string) { FakeSocket.all.push(this); }
  send(d: string) { this.sent.push(JSON.parse(d)); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
  open() { this.readyState = 1; this.onopen?.(); }
}

describe("the socket side", () => {
  const orig = globalThis.WebSocket;
  beforeEach(() => { FakeSocket.all = []; (globalThis as { WebSocket: unknown }).WebSocket = FakeSocket; __resetGatewayWS(); });
  afterEach(() => { __resetGatewayWS(); (globalThis as { WebSocket: unknown }).WebSocket = orig; });

  it("subscribes for signals only, delivers that meeting's frames, and unsubscribes when the last viewer leaves", () => {
    const got: unknown[] = [];
    const off = subscribeMeetingStreams("google_meet", "abc-defg-hij", ["signals"], (f) => got.push(f));
    const sock = FakeSocket.all[0];
    expect(sock).toBeDefined();
    sock.open();
    expect(sock.sent).toContainEqual({ action: "subscribe", meetings: [{ platform: "google_meet", native_id: "abc-defg-hij" }], streams: ["signals"] });
    sock.onmessage?.({ data: JSON.stringify(golden) });
    sock.onmessage?.({ data: JSON.stringify({ ...golden, meeting: { id: "9", platform: "google_meet", native_id: "other" } }) });
    expect(got).toHaveLength(1);
    off();
    expect(sock.sent).toContainEqual({ action: "unsubscribe", meetings: [{ platform: "google_meet", native_id: "abc-defg-hij" }] });
  });

  it("re-sends the subscribe after a reconnect", () => {
    const off = subscribeMeetingStreams("google_meet", "abc-defg-hij", ["signals"], () => {});
    FakeSocket.all[0].open();
    FakeSocket.all[0].close();
    __resetGatewayWS({ keepSubscriptions: true, reconnectNow: true });
    const again = FakeSocket.all[FakeSocket.all.length - 1];
    again.open();
    expect(again.sent).toContainEqual({ action: "subscribe", meetings: [{ platform: "google_meet", native_id: "abc-defg-hij" }], streams: ["signals"] });
    off();
  });
});
