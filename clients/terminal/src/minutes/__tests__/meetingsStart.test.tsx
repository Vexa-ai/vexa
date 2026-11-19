/** THE MEETINGS PAGE AFTER O1 (founder validation, 2026-10-18), mounted.
 *  - "Start a meeting" offers three choices — call a person, start in a room, schedule for later —
 *    with a Vexa call as the default and Meet/Teams/Zoom under "Other options"; it never starts a call
 *    by itself any more;
 *  - schedule is shown disabled with "coming" when rooms are off;
 *  - a live Vexa call's row says who is in now (never the listener), the elapsed time, and "renamed";
 *  - an ended call moves to Recent, saying "ended when everyone left · 12 min". */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";

const store = vi.hoisted(() => ({ meetings: [] as unknown[] }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => store.meetings, useLiveMeetingsLoaded: () => true, refreshMeetings: vi.fn(),
}));
import { MeetingsPage } from "../shell2/MeetingsPage";
import { setFeatureOverride } from "../../platform/features";
import { START_MEETING_EVENT } from "../../platform/events";

const base = { title: "Call", when: "", platform: "vexa_call", participants: [], mentioned: [], actions: [], transcript: [], insights: [] };
const started: unknown[] = [];
const onStart = (e: Event) => started.push((e as CustomEvent).detail);

beforeEach(() => {
  started.length = 0;
  window.addEventListener(START_MEETING_EVENT, onStart);
  const now = Date.now();
  store.meetings = [
    { ...base, id: "1", status: "live", live_status: "active", title_custom: "#pilot · ADFS test window", title: "#pilot · ADFS test window",
      renamed_from: "#pilot call", call_session: "s-room", call_room: "s-room", start_time: new Date(now - 252_000).toISOString() },
    { ...base, id: "2", status: "past", live_status: "completed", title_custom: "Call with Megan", title: "Call with Megan", completion_reason: "left_alone",
      start_time: new Date(now - 30 * 60_000).toISOString(), end_time: new Date(now - 18 * 60_000).toISOString() },
  ];
  globalThis.fetch = vi.fn(async (url: unknown) => {
    const u = String(url);
    const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith("/api/sessions/s-room/call")) return ok({ session_id: "s-room", calls: true, call: { room: "call-1", in_now: [{ subject: "u_m", name: "Megan Ross" }] } });
    if (u.startsWith("/api/people/mentionable")) return ok({ people: [{ subject: "u_m", name: "Megan Ross", workspaces: ["team"] }], agents: [] });
    return ok({});
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); window.removeEventListener(START_MEETING_EVENT, onStart); setFeatureOverride("all"); vi.restoreAllMocks(); });

describe("Start a meeting", () => {
  it("offers three choices and starts nothing by itself", async () => {
    setFeatureOverride({ rooms: false });
    render(<MeetingsPage />);
    fireEvent.click(screen.getByRole("button", { name: /Start a meeting/ }));
    expect(screen.getByText("Call a person")).toBeTruthy();
    expect(screen.getByText("Start in a room")).toBeTruthy();
    expect(screen.getByText("Schedule for later")).toBeTruthy();
    expect(screen.getByText("A Vexa call is the default.")).toBeTruthy();
    expect(screen.getByText(/Other options/)).toBeTruthy();
    // no rooms here: scheduling is shown, disabled, as coming
    expect((screen.getByRole("button", { name: /Schedule for later · coming/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(started).toEqual([]);
  });
});

describe("Live and Recent", () => {
  it("a live call says who is in now, how long, and that it was renamed", async () => {
    render(<MeetingsPage />);
    await waitFor(() => expect(document.querySelector('[data-live-row="1"]')?.textContent).toContain("in now: Megan"));
    const row = document.querySelector('[data-live-row="1"]')!.textContent!;
    expect(row).toContain("#pilot · ADFS test window");
    expect(row).toMatch(/4:1\d/);
    expect(row).toContain("renamed");
  });

  it("an ended call is in Recent: ended when everyone left · 12 min", () => {
    render(<MeetingsPage />);
    const row = document.querySelector('[data-recent-row="2"]')!.textContent!;
    expect(row).toContain("Call with Megan");
    expect(row).toContain("ended when everyone left · 12 min");
  });
});
