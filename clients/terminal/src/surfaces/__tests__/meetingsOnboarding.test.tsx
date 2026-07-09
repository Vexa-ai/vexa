/** Meetings user onboarding (frames 4–5): the three-path empty state, the STANDING slim
 *  calendar card (state-driven — exists exactly while no calendar is connected), and the
 *  connect modal's answer-on-connect behavior. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

vi.mock("../../platform", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useService: () => ({ openTab: vi.fn() }),
}));

import { MeetingsOnboarding, connectOutcome } from "../meetingsOnboarding";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubCalendarApi(opts: { connected: boolean; syncCounts?: { created?: number; updated?: number }; syncError?: string }) {
  const calls: { url: string; method?: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method, body: init?.body as string });
      if (String(url).includes("/api/user/calendar/sync")) {
        if (opts.syncError) return new Response(JSON.stringify({ last_error: opts.syncError }), { status: 200 });
        return new Response(JSON.stringify({ last_sync: new Date().toISOString(), counts: opts.syncCounts ?? {} }), { status: 200 });
      }
      if (String(url).includes("/api/user/calendar")) {
        if (init?.method === "PUT") {
          return new Response(JSON.stringify({ ics_url_set: true, ics_url_masked: "calendar.google.com/…d3f1", auto_join: true }), { status: 200 });
        }
        return new Response(JSON.stringify({ ics_url_set: opts.connected, ics_url_masked: null, auto_join: true }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }),
  );
  return calls;
}

describe("connectOutcome", () => {
  it("sync error → loud", () => {
    const o = connectOutcome({ last_error: "HTTP 401 — that looks like the public address" });
    expect(o.ok).toBe(false);
    expect(o.text).toContain("HTTP 401");
  });
  it("counts → 'N upcoming meetings imported'", () => {
    expect(connectOutcome({ counts: { created: 11, updated: 1 } }).text).toContain("12 upcoming meetings imported");
    expect(connectOutcome({ counts: { created: 1 } }).text).toContain("1 upcoming meeting imported");
  });
  it("zero found → honest, not silent", () => {
    expect(connectOutcome({ counts: {} }).text).toContain("no upcoming meetings with joinable links");
  });
});

describe("slim — the standing calendar affordance", () => {
  it("renders while NO calendar is connected", async () => {
    stubCalendarApi({ connected: false });
    render(<MeetingsOnboarding variant="slim" />);
    await waitFor(() => expect(screen.getByText(/No calendar connected/)).toBeTruthy());
  });

  it("disappears once connected", async () => {
    const calls = stubCalendarApi({ connected: true });
    const { container } = render(<MeetingsOnboarding variant="slim" />);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/user/calendar"))).toBe(true));
    expect(container.textContent).not.toContain("No calendar connected");
  });
});

describe("full — the three-path empty state", () => {
  it("shows all three paths, calendar first, when nothing is connected", async () => {
    stubCalendarApi({ connected: false });
    render(<MeetingsOnboarding variant="full" />);
    await waitFor(() => expect(screen.getByText("Connect your calendar")).toBeTruthy());
    expect(screen.getByText("Plan a meeting")).toBeTruthy();
    expect(screen.getByText("Drop a bot in now")).toBeTruthy();
  });

  it("calendar card retires once connected; plan/drop remain", async () => {
    const calls = stubCalendarApi({ connected: true });
    render(<MeetingsOnboarding variant="full" />);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/user/calendar"))).toBe(true));
    await waitFor(() => expect(screen.getByText(/Calendar connected/)).toBeTruthy());
    expect(screen.queryByText("Connect your calendar")).toBeNull();
    expect(screen.getByText("Plan a meeting")).toBeTruthy();
  });
});

describe("connect modal — teaches the secret address and answers on connect", () => {
  it("walkthrough + paste → PUT + sync-now → reports what it found", async () => {
    const calls = stubCalendarApi({ connected: false, syncCounts: { created: 3 } });
    render(<MeetingsOnboarding variant="slim" />);
    await waitFor(() => screen.getByText("Connect calendar →"));
    fireEvent.click(screen.getByText("Connect calendar →"));

    // the explainer is THERE — secret-address step + the Workspace-admin trap
    expect(screen.getByText(/Secret address in iCal format/)).toBeTruthy();
    expect(screen.getByText(/Workspace admin has it locked/)).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText(/basic\.ics/), {
      target: { value: "https://calendar.google.com/calendar/ical/x/private-abc/basic.ics" },
    });
    fireEvent.click(screen.getByText("Connect"));

    await waitFor(() => expect(screen.getByText(/3 upcoming meetings imported/)).toBeTruthy());
    const put = calls.find((c) => c.url.includes("/api/user/calendar") && c.method === "PUT");
    expect(put).toBeDefined();
    expect(calls.some((c) => c.url.includes("/api/user/calendar/sync") && c.method === "POST")).toBe(true);
  });

  it("first-sync failure is loud, with the server's reason", async () => {
    stubCalendarApi({ connected: false, syncError: "HTTP 401 fetching the feed — that looks like the public address, not the secret one" });
    render(<MeetingsOnboarding variant="slim" />);
    await waitFor(() => screen.getByText("Connect calendar →"));
    fireEvent.click(screen.getByText("Connect calendar →"));
    fireEvent.change(screen.getByPlaceholderText(/basic\.ics/), {
      target: { value: "https://calendar.google.com/calendar/ical/x/private-abc/basic.ics" },
    });
    fireEvent.click(screen.getByText("Connect"));
    await waitFor(() => expect(screen.getByText(/first sync failed/)).toBeTruthy());
    expect(screen.getByText(/first sync failed/).textContent).toContain("public address, not the secret one");
  });
});
