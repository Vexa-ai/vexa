/** The routine strip reads on ONE clock — the viewer's (app.dev 2026-10-14) — and its one line names
 *  the next run as a date. The strip's layout contract (one line, bounded expansion) lives in
 *  `routineStrip.test.tsx`; the sentence-wide header this file used to pin was replaced by that line
 *  (founder, 2026-10-14: "total mess here"). */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen, fireEvent } from "@testing-library/react";
import { RoutineBar } from "../RoutineBar";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("routine strip times", () => {
  it("the wake time and the next run read on one clock — the viewer's", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(Date.UTC(2026, 9, 14, 9, 43, 0)));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ routines: [
      { name: "nightly-chat-digest", trigger: { kind: "schedule", cron: "0 23 * * *" }, steps: ["agent_run"],
        enabled: true, status: "active", runs_today: 74, runs_per_day: 500 },
    ] }))));
    const { container } = render(<RoutineBar routine={{ flow: "nightly-chat-digest", scope: "user:1", version: 1 }} />);
    await screen.findByText(/^Routine · every day/);
    const line = container.querySelector("[data-routine-line]")!.textContent!;
    const [, wakeAt, nextAt] = line.match(/^Routine · every day (\d\d:\d\d) · next \S+ (\d\d:\d\d) · /)!;
    expect(wakeAt).toBe(nextAt);
    if (!/^(UTC|Etc\/UTC)$/.test(Intl.DateTimeFormat().resolvedOptions().timeZone)) expect(line).not.toMatch(/UTC/);
    expect(line).not.toMatch(/in \d+ h/);
    fireEvent.click(container.querySelector("[data-routine-expand]")!);
    fireEvent.click(container.querySelector("[data-routine-details]")!);
    expect(container.querySelector("[data-routine-facts]")!.textContent).toContain("74/500 runs today");
  });
});
