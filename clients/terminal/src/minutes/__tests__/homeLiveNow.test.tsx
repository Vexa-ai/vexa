/** EVERY LIVE MEETING AT THE TOP OF HOME (founder, live app.dev 2026-10-18: "ongoing meeting should be
 *  there I think"). His live Vexa call was in Meetings (the meetings store) but not on Home, whose Now
 *  strip read only the workspace tiles. Pinned: a live call the tiles never reached is on Home with its
 *  name, who is in and the elapsed time; a live bot meeting counts too; Rejoin rejoins a Vexa call;
 *  when this tab is already in the call the row says "You're in this call" with Open (the dock holds
 *  the controls); a call both sources know is listed once; the strip shows on shell2's Home. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const store = vi.hoisted(() => ({ meetings: [] as unknown[] }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => store.meetings, useLiveMeetingsLoaded: () => true, refreshMeetings: vi.fn(), liveMeetingsNow: () => store.meetings,
}));
vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => undefined, Toaster: () => null }));
import { NowStrip } from "../desktop/home2/NowStrip";
import { nowLiveRows } from "../desktop/home2/nowLive";
import { resetAttention } from "../desktop/home2/attentionApi";
import { setFeatureOverride } from "../../platform/features";
import { CALL_START_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import { enterCall, leaveCall, type CallDeps } from "../callState";
import type { WorkspaceItem } from "../../platform/desktop";

const base = { title: "Call", when: "", participants: [], mentioned: [], actions: [], transcript: [], insights: [] };
const vexaCall = { ...base, id: "41", status: "live", live_status: "active", platform: "vexa_call", title_custom: "Thinking out loud",
  title: "Thinking out loud", call_session: "s-think", start_time: new Date(Date.now() - 125_000).toISOString() };
const botMeeting = { ...base, id: "42", status: "live", live_status: "active", platform: "Google Meet", title_custom: "OeNB weekly sync",
  title: "OeNB weekly sync", start_time: new Date(Date.now() - 600_000).toISOString() };
const tile = (over: Partial<WorkspaceItem> = {}): WorkspaceItem => ({
  kind: "workspace", ref: "workspace:alpha", workspace_kind: "shared", slug: "alpha", id: "alpha",
  name: "Alpha", access: "owner", write: true, members: [], last_activity: null, last_call: null, live_call: null, ...over,
});
function heard(name: string) {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}
const deps: CallDeps = {
  getCall: async () => ({ session_id: "s-think", calls: true, call: null }),
  startCall: async () => ({ session_id: "s-think", calls: true, call: null }),
  joinCall: async () => ({ session_id: "s-think", url: "wss://x", token: "t", role: "owner",
    call: { room: "call-1", meeting_id: 41, owner: "me", url: "wss://x", started: 0, transcribed: true, participants: 2 } }),
};

beforeEach(() => {
  store.meetings = [vexaCall, botMeeting];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    const ok = (x: unknown) => new Response(JSON.stringify(x), { status: 200 });
    if (u === "/api/desktop/attention") return ok({ generated_at: 1, needs: [], workspaces: {} });
    if (u.startsWith("/api/sessions/s-think/call")) return ok({ session_id: "s-think", calls: true, call: { room: "call-1", in_now: [{ subject: "u_m", name: "Megan Ross" }] } });
    return ok({});
  }));
  setFeatureOverride({ home2: true });
});
afterEach(() => { cleanup(); act(() => leaveCall()); vi.unstubAllGlobals(); resetAttention(); setFeatureOverride("all"); });

describe("live meetings on Home", () => {
  it("a live Vexa call the tiles never reached is there: name, who is in, how long, Rejoin", async () => {
    render(<NowStrip workspaces={[tile()]} />);
    await waitFor(() => expect(document.querySelector('[data-live-now="m:41"]')?.textContent).toContain("in now: Megan"));
    const row = document.querySelector('[data-live-now="m:41"]')!.textContent!;
    expect(row).toContain("Thinking out loud");
    expect(row).toMatch(/2:0\d/);
    const start = heard(CALL_START_EVENT);
    fireEvent.click(screen.getAllByRole("button", { name: "Rejoin" })[0]);
    start.off();
    expect(start.got).toEqual([{ session: "s-think" }]);
  });

  it("a live bot meeting counts too, and opens", async () => {
    render(<NowStrip workspaces={[tile()]} />);
    const row = await waitFor(() => document.querySelector('[data-live-now="m:42"]')!);
    expect(row.textContent).toContain("OeNB weekly sync");
    const open = heard(OPEN_MEETING_EVENT);
    fireEvent.click(row.querySelector('[data-verb="Open"]')!);
    open.off();
    expect(open.got).toEqual([{ ref: "42" }]);
  });

  it("already connected: 'You're in this call' with Open, no Rejoin — the dock has the controls", async () => {
    await act(async () => { await enterCall("s-think", "Thinking out loud", deps); });
    render(<NowStrip workspaces={[tile()]} />);
    const row = await waitFor(() => document.querySelector('[data-live-now="m:41"]')!);
    expect(row.hasAttribute("data-in-this")).toBe(true);
    expect(row.textContent).toContain("You're in this call");
    expect(row.querySelector('[data-verb="Rejoin"]')).toBeNull();
    const open = heard(OPEN_SESSION_EVENT);
    fireEvent.click(row.querySelector('[data-verb="Open"]')!);
    open.off();
    expect(open.got).toEqual([{ session: "s-think" }]);
  });

  it("a call both the meetings store and a workspace tile know is listed once", () => {
    const rows = nowLiveRows([{ session: "s-think", meeting: "41", line: "Call", people: 2, since: 1, where: "Alpha" },
      { session: "s-other", line: "#pilot call", people: 1, since: 2, where: "Beta" }], store.meetings as never, null);
    expect(rows.filter((r) => r.meetingId === "41").length).toBe(1);
    expect(rows.find((r) => r.meetingId === "41")!.where).toBe("Alpha");
    expect(rows.length).toBe(3);
  });
});
