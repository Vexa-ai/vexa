/** Behavioral test for the meeting-header bot control's WS gating (issue #674 C1).
 *
 *  The header state and the control's enabled-ness are a pure function of (row status,
 *  WS-connected): connected+active → Live + enabled "Stop bot"; disconnected → indeterminate
 *  ("Reconnecting…") + DISABLED control — a stale-live snapshot can never present an actionable
 *  "Stop bot"; no active meeting → the send/resend control.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { BotControls, meetingHeaderState } from "../meeting";
import type { MeetingMock } from "../meetingModel";

const NATIVE = "abc-defg-hij";

function row(live_status: string, status: "live" | "past" = live_status === "active" ? "live" : "past"): MeetingMock {
  return {
    id: NATIVE, native_id: NATIVE, title: "Google Meet · " + NATIVE, when: "now",
    status, live_status, platform: "Google Meet", has_recording: false,
    docs: [], participants: [], mentioned: [], actions: [], transcript: [], insights: [],
  } as MeetingMock;
}

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("meetingHeaderState — pure (row, connected) → header", () => {
  it("connected + live row → live", () => expect(meetingHeaderState(row("active"), true)).toBe("live"));
  it("DISCONNECTED + live row → reconnecting (never a stale Live)", () => expect(meetingHeaderState(row("active"), false)).toBe("reconnecting"));
  it("terminal row → recap (either way)", () => {
    expect(meetingHeaderState(row("completed"), true)).toBe("recap");
    expect(meetingHeaderState(row("completed"), false)).toBe("recap");
  });
  it("unresolved row → connecting", () => expect(meetingHeaderState(undefined, true)).toBe("connecting"));
});

describe("BotControls — enabled-ness follows WS connectivity", () => {
  it("connected + active → 'Stop bot' enabled", () => {
    render(<BotControls m={row("active")} connected={true} />);
    const btn = screen.getByRole("button", { name: "Stop bot" });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });
  it("disconnected + (stale) active → 'Stop bot' DISABLED with the reconnect hint", () => {
    render(<BotControls m={row("active")} connected={false} />);
    const btn = screen.getByRole("button", { name: "Stop bot" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("title")).toMatch(/reconnecting/i);
  });
  it("no active meeting (terminal row) → the send-again control, not Stop", () => {
    render(<BotControls m={row("completed")} connected={true} />);
    expect(screen.queryByRole("button", { name: "Stop bot" })).toBeNull();
    expect(screen.getByRole("button", { name: "Send bot again" })).toBeTruthy();
  });
});
