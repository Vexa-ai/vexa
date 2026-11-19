/** The reload bar itself (PRD decision 39): what it shows, when, and what it refuses to do.
 *
 *  The refusals are the point. An automatic reload would throw away an unsent chat message to fix
 *  a staleness the person may not care about, and a bar that appears before there is news is a bar
 *  nobody reads by the time there is. So: nothing until a second reading disagrees with the first,
 *  and the page moves only when a human clicks.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { VersionBar } from "../VersionBar";
import type { VersionReport } from "../versionWatch";

const report = (over: Partial<VersionReport> = {}): VersionReport => ({
  terminal: { build: "line-aaaa", agent_api: 1 },
  server: { sha: "line-aaaa", api: 1 },
  paired: true,
  ...over,
});

/** Serve a queue of readings to the component's own /api/version polls; the last one repeats. */
function serve(readings: (VersionReport | "fail")[]) {
  let i = 0;
  const fetchMock = vi.fn(async () => {
    const r = readings[Math.min(i++, readings.length - 1)];
    if (r === "fail") throw new Error("fetch failed");
    return new Response(JSON.stringify(r), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const bar = () => screen.queryByText("A new version is ready — reload");

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("VersionBar", () => {
  it("shows nothing while the deployment has not moved", async () => {
    serve([report(), report()]);
    render(<VersionBar />);
    await vi.advanceTimersByTimeAsync(61_000);
    expect(bar()).toBeNull();
  });

  it("appears once the server underneath the tab changed", async () => {
    serve([report(), report({ server: { sha: "line-bbbb", api: 1 } })]);
    render(<VersionBar />);
    await vi.advanceTimersByTimeAsync(61_000);
    await waitFor(() => expect(bar()).not.toBeNull());
  });

  it("reloads ONLY on the click", async () => {
    const reload = vi.fn();
    serve([report(), report({ terminal: { build: "line-bbbb", agent_api: 1 } })]);
    render(<VersionBar reload={reload} />);
    await vi.advanceTimersByTimeAsync(61_000);
    await waitFor(() => expect(bar()).not.toBeNull());
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Reload"));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("stays quiet through a failed poll — a swap's own gap is not news", async () => {
    serve([report(), "fail", report()]);
    render(<VersionBar />);
    await vi.advanceTimersByTimeAsync(121_000);
    expect(bar()).toBeNull();
  });

  it("stops polling once it has something to say", async () => {
    const f = serve([report(), report({ server: { sha: "line-bbbb", api: 1 } })]);
    render(<VersionBar />);
    await vi.advanceTimersByTimeAsync(61_000);
    await waitFor(() => expect(bar()).not.toBeNull());
    const settled = f.mock.calls.length;
    await vi.advanceTimersByTimeAsync(300_000);
    expect(f.mock.calls.length).toBe(settled);
  });

  it("re-checks when the tab regains focus, without waiting out the interval", async () => {
    const f = serve([report(), report()]);
    render(<VersionBar />);
    await waitFor(() => expect(f.mock.calls.length).toBe(1));
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(f.mock.calls.length).toBe(2));
  });
});
